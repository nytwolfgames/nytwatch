#include "NytwatchSessionWriter.h"

#include "Misc/Guid.h"
#include "Misc/DateTime.h"
#include "Misc/App.h"
#include "IWebSocket.h"
#include "WebSocketsModule.h"
#include "Framework/Notifications/NotificationManager.h"
#include "Widgets/Notifications/SNotificationList.h"

DEFINE_LOG_CATEGORY_STATIC(LogNytwatchWriter, Log, All);

// ---------------------------------------------------------------------------
// JSON helpers
// ---------------------------------------------------------------------------

FString FNytwatchSessionWriter::EscapeJsonString(const FString& Str)
{
    FString Out = Str;
    // Order matters: backslash first
    Out.ReplaceInline(TEXT("\\"), TEXT("\\\\"), ESearchCase::CaseSensitive);
    Out.ReplaceInline(TEXT("\""), TEXT("\\\""), ESearchCase::CaseSensitive);
    Out.ReplaceInline(TEXT("\n"), TEXT("\\n"),  ESearchCase::CaseSensitive);
    Out.ReplaceInline(TEXT("\r"), TEXT("\\r"),  ESearchCase::CaseSensitive);
    Out.ReplaceInline(TEXT("\t"), TEXT("\\t"),  ESearchCase::CaseSensitive);
    return Out;
}

// ---------------------------------------------------------------------------
// Open  (game thread)
// ---------------------------------------------------------------------------

void FNytwatchSessionWriter::Open(const FNytwatchConfig& Config)
{
    // Reset all state from any previous session
    bIsOpen            = false;
    bReady             = false;
    bCapReached        = false;
    bNeedsReconnect    = false;
    bPendingDisconnect = false;
    ReconnectAttempts  = 0;
    TotalEventCount    = 0;

    WebSocketUrl = Config.WebSocketUrl;
    if (WebSocketUrl.IsEmpty())
    {
        UE_LOG(LogNytwatchWriter, Error,
            TEXT("[NytwatchAgent] NytwatchConfig.json has no tracking_ws_url — tracking disabled."));
        return;
    }

    // Session ID and timestamps
    SessionId    = FGuid::NewGuid().ToString(EGuidFormats::DigitsWithHyphens);
    StartedAtDT  = FDateTime::UtcNow();
    StartedAtStr = StartedAtDT.ToString(TEXT("%Y-%m-%dT%H:%M:%SZ"));
    UEProjectName = FApp::GetProjectName();

    // Build armed system names JSON array  e.g. ["Combat","AI"]
    TArray<FString> SysNames;
    for (const FNytwatchSystemConfig& S : Config.ArmedSystems)
        SysNames.Add(FString::Printf(TEXT("\"%s\""), *EscapeJsonString(S.SystemName)));
    ArmedSystemsJson = TEXT("[") + FString::Join(SysNames, TEXT(",")) + TEXT("]");

    bIsOpen = true;
    CreateAndConnect();
}

// ---------------------------------------------------------------------------
// CreateAndConnect  (game thread)
// ---------------------------------------------------------------------------

void FNytwatchSessionWriter::CreateAndConnect()
{
    if (!FModuleManager::Get().IsModuleLoaded(TEXT("WebSockets")))
        FModuleManager::Get().LoadModule(TEXT("WebSockets"));

    WebSocket = FWebSocketsModule::Get().CreateWebSocket(WebSocketUrl, TEXT(""));

    WebSocket->OnConnected().AddRaw(this, &FNytwatchSessionWriter::OnConnected);
    WebSocket->OnConnectionError().AddRaw(this, &FNytwatchSessionWriter::OnConnectionError);
    WebSocket->OnClosed().AddRaw(this, &FNytwatchSessionWriter::OnClosed);
    WebSocket->OnMessage().AddRaw(this, &FNytwatchSessionWriter::OnMessage);

    WebSocket->Connect();
    UE_LOG(LogNytwatchWriter, Log,
        TEXT("[NytwatchAgent] Connecting to %s"), *WebSocketUrl);
}

// ---------------------------------------------------------------------------
// WebSocket callbacks  (game thread)
// ---------------------------------------------------------------------------

void FNytwatchSessionWriter::OnConnected()
{
    bNeedsReconnect   = false;
    ReconnectAttempts = 0;
    SendSessionOpen();
    bReady = true;
    UE_LOG(LogNytwatchWriter, Log,
        TEXT("[NytwatchAgent] WebSocket connected — session %s"), *SessionId);
}

void FNytwatchSessionWriter::OnConnectionError(const FString& Error)
{
    UE_LOG(LogNytwatchWriter, Warning,
        TEXT("[NytwatchAgent] WebSocket connection error: %s"), *Error);
    bIsOpen = false;  // signal to the subsystem tick that connection failed
    bReady  = false;
}

void FNytwatchSessionWriter::OnClosed(int32 StatusCode, const FString& Reason, bool bWasClean)
{
    if (!bIsOpen) return; // closed by our own Close() or EmergencyClose() — ignore

    UE_LOG(LogNytwatchWriter, Warning,
        TEXT("[NytwatchAgent] WebSocket closed unexpectedly (code=%d reason=%s) — will reconnect"),
        StatusCode, *Reason);
    bReady = false;
    if (ReconnectAttempts < MaxReconnectAttempts)
        bNeedsReconnect = true;
    else
        UE_LOG(LogNytwatchWriter, Warning,
            TEXT("[NytwatchAgent] Max reconnect attempts reached — giving up."));
}

void FNytwatchSessionWriter::OnMessage(const FString& /*Msg*/)
{
    // Server sends no messages to the plugin in this protocol.
}

// ---------------------------------------------------------------------------
// TryReconnect  (game thread, called from subsystem tick)
// ---------------------------------------------------------------------------

void FNytwatchSessionWriter::TryReconnect()
{
    bNeedsReconnect = false;
    ++ReconnectAttempts;

    if (WebSocket.IsValid())
        WebSocket.Reset(); // destroy old socket before creating a new one

    UE_LOG(LogNytwatchWriter, Log,
        TEXT("[NytwatchAgent] Reconnect attempt %d/%d for session %s"),
        ReconnectAttempts, MaxReconnectAttempts, *SessionId);

    CreateAndConnect();
}

// ---------------------------------------------------------------------------
// Abort  (game thread)
// ---------------------------------------------------------------------------

void FNytwatchSessionWriter::Abort()
{
    bIsOpen = false;
    bReady  = false;
    if (WebSocket.IsValid())
    {
        WebSocket->Close();
        WebSocket.Reset();
    }
}

// ---------------------------------------------------------------------------
// SendBatch  (game thread, each tick)
// ---------------------------------------------------------------------------

void FNytwatchSessionWriter::SendBatch(const TArray<FNytwatchEvent>& Events, float PIEElapsedSeconds)
{
    if (!bReady || bCapReached || Events.Num() == 0) return;

    const FString Json = BuildBatchJson(Events, PIEElapsedSeconds);
    WebSocket->Send(Json);

    TotalEventCount += Events.Num();

    if (TotalEventCount >= EventCap)
    {
        bCapReached = true;

        const FString Msg = TEXT("Nytwatch: event cap reached — recording stopped for this session.");
        UE_LOG(LogNytwatchWriter, Warning, TEXT("[NytwatchAgent] %s"), *Msg);

        FNotificationInfo Info(FText::FromString(Msg));
        Info.bFireAndForget       = true;
        Info.ExpireDuration       = 8.0f;
        Info.bUseSuccessFailIcons = true;
        FSlateNotificationManager::Get().AddNotification(Info);
    }
}

// ---------------------------------------------------------------------------
// Close  (game thread, normal end)
// ---------------------------------------------------------------------------

void FNytwatchSessionWriter::Close(float PIEElapsedSeconds)
{
    if (!bIsOpen) return;
    bIsOpen = false;
    bReady  = false;

    if (WebSocket.IsValid() && WebSocket->IsConnected())
    {
        SendSessionClose(PIEElapsedSeconds, TEXT("normal"));
        // Do NOT call WebSocket->Close() here — the send is async and the
        // close frame would race against the session_close data frame.
        // The caller must invoke FlushAndDisconnect() ~100 ms later.
        bPendingDisconnect = true;
    }
    else
    {
        WebSocket.Reset();
    }

    UE_LOG(LogNytwatchWriter, Log,
        TEXT("[NytwatchAgent] Session closed normally — %d events, session %s"),
        TotalEventCount, *SessionId);
}

// ---------------------------------------------------------------------------
// FlushAndDisconnect  (game thread, called ~100 ms after Close)
// ---------------------------------------------------------------------------

void FNytwatchSessionWriter::FlushAndDisconnect()
{
    bPendingDisconnect = false;
    if (WebSocket.IsValid())
    {
        WebSocket->Close();
        WebSocket.Reset();
    }
}

// ---------------------------------------------------------------------------
// EmergencyClose  (crash-handler context)
// ---------------------------------------------------------------------------

void FNytwatchSessionWriter::EmergencyClose(float PIEElapsedSeconds)
{
    if (!bIsOpen) return;
    bIsOpen = false;
    bReady  = false;

    if (WebSocket.IsValid() && WebSocket->IsConnected())
    {
        SendSessionClose(PIEElapsedSeconds, TEXT("crash"));
        WebSocket->Close();
    }

    WebSocket.Reset();

    UE_LOG(LogNytwatchWriter, Warning,
        TEXT("[NytwatchAgent] Emergency close — session %s marked as crashed"), *SessionId);
}

// ---------------------------------------------------------------------------
// SendSessionOpen  (called from OnConnected)
// ---------------------------------------------------------------------------

void FNytwatchSessionWriter::SendSessionOpen()
{
    const FString Json = FString::Printf(
        TEXT("{\"type\":\"session_open\","
             "\"session_id\":\"%s\","
             "\"ue_project_name\":\"%s\","
             "\"plugin_version\":\"" NYTWATCH_PLUGIN_VERSION "\","
             "\"started_at\":\"%s\","
             "\"armed_systems\":%s}"),
        *SessionId,
        *EscapeJsonString(UEProjectName),
        *StartedAtStr,
        *ArmedSystemsJson
    );
    WebSocket->Send(Json);
}

// ---------------------------------------------------------------------------
// SendSessionClose
// ---------------------------------------------------------------------------

void FNytwatchSessionWriter::SendSessionClose(float DurationSeconds, const FString& EndReason)
{
    const FString EndedAt = FDateTime::UtcNow().ToString(TEXT("%Y-%m-%dT%H:%M:%SZ"));
    const int32   DurS    = FMath::Max(0, FMath::RoundToInt(DurationSeconds));

    const FString Json = FString::Printf(
        TEXT("{\"type\":\"session_close\","
             "\"session_id\":\"%s\","
             "\"ended_at\":\"%s\","
             "\"duration_seconds\":%d,"
             "\"end_reason\":\"%s\"}"),
        *SessionId,
        *EndedAt,
        DurS,
        *EndReason
    );
    WebSocket->Send(Json);
}

// ---------------------------------------------------------------------------
// BuildBatchJson
// ---------------------------------------------------------------------------

FString FNytwatchSessionWriter::BuildBatchJson(const TArray<FNytwatchEvent>& Events, float T) const
{
    FString EventsArray;
    EventsArray.Reserve(Events.Num() * 100);

    for (int32 i = 0; i < Events.Num(); ++i)
    {
        if (i > 0) EventsArray += TEXT(",");

        const FNytwatchEvent& E = Events[i];
        EventsArray += FString::Printf(
            TEXT("{\"sys\":\"%s\",\"obj\":\"%s\",\"prop\":\"%s\","
                 "\"old\":\"%s\",\"new\":\"%s\",\"num\":%d}"),
            *EscapeJsonString(E.SystemName.ToString()),
            *EscapeJsonString(E.ObjectName),
            *EscapeJsonString(E.PropertyName.ToString()),
            *EscapeJsonString(E.OldValue),
            *EscapeJsonString(E.NewValue),
            E.bIsNumeric ? 1 : 0
        );
    }

    return FString::Printf(
        TEXT("{\"type\":\"event_batch\","
             "\"session_id\":\"%s\","
             "\"t\":%.2f,"
             "\"events\":[%s]}"),
        *SessionId, T, *EventsArray
    );
}
