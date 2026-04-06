#include "NytwatchSessionWriter.h"

#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/Guid.h"
#include "Misc/DateTime.h"
#include "Misc/App.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformFileManager.h"
#include "GenericPlatform/GenericPlatformFile.h"
#include "Framework/Notifications/NotificationManager.h"
#include "Widgets/Notifications/SNotificationList.h"

DEFINE_LOG_CATEGORY_STATIC(LogNytwatchWriter, Log, All);

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

FString FNytwatchSessionWriter::StripUEPrefix(const FString& Name)
{
    if (Name.Len() >= 2
        && (Name[0] == TEXT('A') || Name[0] == TEXT('U'))
        && FChar::IsUpper(Name[1]))
    {
        return Name.Mid(1);
    }
    return Name;
}

FString FNytwatchSessionWriter::TrimFloat(double Val)
{
    // Guard against -0
    if (FMath::Abs(Val) < 1e-9) return TEXT("0");

    FString S = FString::Printf(TEXT("%.6f"), Val);

    // Strip trailing zeros after decimal point
    if (S.Contains(TEXT(".")))
    {
        while (S.EndsWith(TEXT("0"))) S.LeftChopInline(1);
        if (S.EndsWith(TEXT(".")))    S.LeftChopInline(1);
    }
    return S;
}

FString FNytwatchSessionWriter::FormatBool(const FString& Val)
{
    if (Val == TEXT("True"))  return TEXT("T");
    if (Val == TEXT("False")) return TEXT("F");
    return Val;
}

// ---------------------------------------------------------------------------
// Open
// ---------------------------------------------------------------------------

void FNytwatchSessionWriter::Open(const FNytwatchConfig& Config, const FString& ProjectDir)
{
    bIsOpen        = false;
    bCapReached    = false;
    TotalEventCount = 0;
    Buffer.Reset();

    // Directories
    const FString NytwatchDir  = FPaths::Combine(ProjectDir, TEXT("Saved"), TEXT("Nytwatch"));
    const FString SessionsDir  = FPaths::Combine(NytwatchDir, TEXT("Sessions"));
    IFileManager::Get().MakeDirectory(*NytwatchDir,  true);
    IFileManager::Get().MakeDirectory(*SessionsDir,  true);

    // Session ID
    SessionId = FGuid::NewGuid().ToString(EGuidFormats::DigitsWithHyphens);
    SessionFilePath = FPaths::Combine(SessionsDir, SessionId + TEXT(".md"));

    // Timestamps
    StartedAtDT  = FDateTime::UtcNow();
    StartedAtStr = StartedAtDT.ToString(TEXT("%Y-%m-%dT%H:%M:%SZ"));

    // Project name
    UEProjectName = FApp::GetProjectName();

    // systems_tracked JSON array
    TArray<FString> SysNames;
    for (const FNytwatchSystemConfig& S : Config.ArmedSystems)
    {
        SysNames.Add(FString::Printf(TEXT("\"%s\""), *S.SystemName));
    }
    SystemsTrackedJson = TEXT("[") + FString::Join(SysNames, TEXT(", ")) + TEXT("]");

    // Build header (placeholders for fields only known at Close time)
    const FString HeadingDate = StartedAtDT.ToString(TEXT("%Y-%m-%d %H:%M:%S"));

    const FString Header = FString::Printf(
        TEXT("---\n")
        TEXT("session_id: %s\n")
        TEXT("ue_project_name: %s\n")
        TEXT("plugin_version: " NYTWATCH_PLUGIN_VERSION "\n")
        TEXT("started_at: %s\n")
        TEXT("ended_at: __ENDED_AT__\n")
        TEXT("duration_seconds: __DURATION__\n")
        TEXT("systems_tracked: %s\n")
        TEXT("event_count: __EVENT_COUNT__\n")
        TEXT("---\n")
        TEXT("\n")
        TEXT("> This is a Nytwatch gameplay session log from Unreal Engine 5. "
             "It records UObject property changes captured during a Play-In-Editor session.\n")
        TEXT("> Format: one line per object. Properties separated by `|`. "
             "Numeric properties use delta encoding: `PropName:InitialValue +N@t -N@t` "
             "where `t` is seconds from session start. "
             "Non-numeric properties (enum, string, bool, vector) use transition chains: "
             "`PropName:From\u2192To@t`. "
             "Booleans abbreviated as T/F. "
             "UE class prefixes (A/U) are stripped from object names. "
             "Objects with no recorded changes are omitted.\n")
        TEXT("\n")
        TEXT("# %s \u2014 %s\n")
        TEXT("\n"),
        *SessionId,
        *UEProjectName,
        *StartedAtStr,
        *SystemsTrackedJson,
        *UEProjectName,
        *HeadingDate
    );

    const bool bOk = FFileHelper::SaveStringToFile(
        Header, *SessionFilePath,
        FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);

    if (!bOk)
    {
        UE_LOG(LogNytwatchWriter, Error,
            TEXT("[NytwatchAgent] Failed to create session file: %s"), *SessionFilePath);
        return;
    }

    bIsOpen = true;
    UE_LOG(LogNytwatchWriter, Log,
        TEXT("[NytwatchAgent] Session file opened: %s"), *SessionFilePath);
}

// ---------------------------------------------------------------------------
// AppendEvent
// ---------------------------------------------------------------------------

void FNytwatchSessionWriter::AppendEvent(const FNytwatchEvent& Event)
{
    if (!bIsOpen || bCapReached) return;

    Buffer.Add(Event);
    ++TotalEventCount;

    if (TotalEventCount >= EventCap)
    {
        bCapReached = true;
        FlushBuffer();

        const FString Msg = TEXT("Nytwatch: event buffer limit reached — recording paused for this session.");
        UE_LOG(LogNytwatchWriter, Warning, TEXT("[NytwatchAgent] %s"), *Msg);

        FNotificationInfo Info(FText::FromString(Msg));
        Info.bFireAndForget = true;
        Info.ExpireDuration = 8.0f;
        FSlateNotificationManager::Get().AddNotification(Info);
        return;
    }

    if (Buffer.Num() >= FlushThreshold)
    {
        FlushBuffer();
    }
}

// ---------------------------------------------------------------------------
// BuildFlushBlock — serialise current buffer into a markdown string
// ---------------------------------------------------------------------------

FString FNytwatchSessionWriter::BuildFlushBlock() const
{
    if (Buffer.Num() == 0) return FString();

    // ----------------------------------------------------------------
    // Group events preserving insertion order:
    //   SystemName -> ObjectDisplayName -> PropertyName -> [Events]
    // ----------------------------------------------------------------
    TArray<FName>                                              SystemOrder;
    TMap<FName, TArray<FString>>                               ObjOrder;
    TMap<FName, TMap<FString, TArray<FName>>>                  PropOrder;
    TMap<FName, TMap<FString, TMap<FName, TArray<FNytwatchEvent>>>> EventData;

    for (const FNytwatchEvent& Evt : Buffer)
    {
        const FString DisplayObj = StripUEPrefix(Evt.ObjectName);

        if (!EventData.Contains(Evt.SystemName))
        {
            SystemOrder.Add(Evt.SystemName);
            ObjOrder.Add(Evt.SystemName,  {});
            PropOrder.Add(Evt.SystemName, {});
            EventData.Add(Evt.SystemName, {});
        }

        auto& ObjMap   = EventData[Evt.SystemName];
        auto& ObjNames = ObjOrder[Evt.SystemName];
        auto& PMap     = PropOrder[Evt.SystemName];

        if (!ObjMap.Contains(DisplayObj))
        {
            ObjNames.Add(DisplayObj);
            PMap.Add(DisplayObj,   {});
            ObjMap.Add(DisplayObj, {});
        }

        auto& PropEvtMap = ObjMap[DisplayObj];
        auto& PropNames  = PMap[DisplayObj];

        if (!PropEvtMap.Contains(Evt.PropertyName))
        {
            PropNames.Add(Evt.PropertyName);
            PropEvtMap.Add(Evt.PropertyName, {});
        }

        PropEvtMap[Evt.PropertyName].Add(Evt);
    }

    // ----------------------------------------------------------------
    // Render
    // ----------------------------------------------------------------
    FString Out;
    Out.Reserve(Buffer.Num() * 80);

    for (const FName& SysName : SystemOrder)
    {
        Out += FString::Printf(TEXT("## %s\n\n"), *SysName.ToString());

        for (const FString& ObjName : ObjOrder[SysName])
        {
            const auto& PropEvtMap = EventData[SysName][ObjName];
            const auto& PropNames  = PropOrder[SysName][ObjName];

            TArray<FString> PropStrings;
            PropStrings.Reserve(PropNames.Num());

            for (const FName& PropName : PropNames)
            {
                const TArray<FNytwatchEvent>& Events = PropEvtMap[PropName];
                FString PropStr;

                if (Events[0].bIsNumeric)
                {
                    // Delta encoding: PropName:InitialValue +Delta@t -Delta@t ...
                    const double InitVal = FCString::Atod(*Events[0].OldValue);
                    PropStr = FString::Printf(TEXT("%s:%s"),
                        *PropName.ToString(), *TrimFloat(InitVal));

                    for (const FNytwatchEvent& E : Events)
                    {
                        const double OldN  = FCString::Atod(*E.OldValue);
                        const double NewN  = FCString::Atod(*E.NewValue);
                        const double Delta = NewN - OldN;
                        const FString Sign = (Delta >= 0) ? TEXT("+") : TEXT("");
                        PropStr += FString::Printf(TEXT(" %s%s@%s"),
                            *Sign, *TrimFloat(Delta), *TrimFloat((double)E.TimeSeconds));
                    }
                }
                else
                {
                    // Transition chain: PropName:Old→New@t→New@t ...
                    // Unicode right arrow: U+2192
                    const FString FirstOld = FormatBool(Events[0].OldValue);
                    const FString FirstNew = FormatBool(Events[0].NewValue);
                    PropStr = FString::Printf(TEXT("%s:%s\u2192%s@%s"),
                        *PropName.ToString(),
                        *FirstOld, *FirstNew,
                        *TrimFloat((double)Events[0].TimeSeconds));

                    for (int32 i = 1; i < Events.Num(); ++i)
                    {
                        PropStr += FString::Printf(TEXT("\u2192%s@%s"),
                            *FormatBool(Events[i].NewValue),
                            *TrimFloat((double)Events[i].TimeSeconds));
                    }
                }

                PropStrings.Add(MoveTemp(PropStr));
            }

            // Pad object name to 20 chars for column alignment
            FString PaddedName = ObjName;
            while (PaddedName.Len() < 20) PaddedName += TEXT(" ");

            Out += PaddedName
                + TEXT("| ")
                + FString::Join(PropStrings, TEXT(" | "))
                + TEXT("\n");
        }

        Out += TEXT("\n");
    }

    return Out;
}

// ---------------------------------------------------------------------------
// FlushBuffer
// ---------------------------------------------------------------------------

void FNytwatchSessionWriter::FlushBuffer()
{
    if (Buffer.Num() == 0) return;

    const FString Block = BuildFlushBlock();
    if (!Block.IsEmpty())
    {
        // FFileHelper::SaveStringToFile does not support append mode.
        // Use IFileManager directly to open with FILEWRITE_Append.
        IFileHandle* Handle = FPlatformFileManager::Get().GetPlatformFile().OpenWrite(
            *SessionFilePath, /*bAppend=*/true, /*bAllowRead=*/true);
        if (Handle)
        {
            FTCHARToUTF8 Utf8(*Block);
            Handle->Write(reinterpret_cast<const uint8*>(Utf8.Get()), Utf8.Length());
            delete Handle;
        }
        else
        {
            UE_LOG(LogNytwatchWriter, Warning,
                TEXT("[NytwatchAgent] Failed to open session file for append: %s"),
                *SessionFilePath);
        }
    }

    Buffer.Reset();
}

// ---------------------------------------------------------------------------
// Close
// ---------------------------------------------------------------------------

void FNytwatchSessionWriter::Close(const FString& ProjectDir)
{
    if (!bIsOpen) return;

    FlushBuffer();

    // --- Backfill header placeholders ---------------------------------------
    const FDateTime EndedAtDT  = FDateTime::UtcNow();
    const FString   EndedAtStr = EndedAtDT.ToString(TEXT("%Y-%m-%dT%H:%M:%SZ"));
    const int32     DurationS  = (int32)(EndedAtDT - StartedAtDT).GetTotalSeconds();

    FString Content;
    if (FFileHelper::LoadFileToString(Content, *SessionFilePath))
    {
        Content.ReplaceInline(TEXT("__ENDED_AT__"),    *EndedAtStr,                         ESearchCase::CaseSensitive);
        Content.ReplaceInline(TEXT("__DURATION__"),    *FString::FromInt(DurationS),         ESearchCase::CaseSensitive);
        Content.ReplaceInline(TEXT("__EVENT_COUNT__"), *FString::FromInt(TotalEventCount),   ESearchCase::CaseSensitive);

        FFileHelper::SaveStringToFile(Content, *SessionFilePath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    }
    else
    {
        UE_LOG(LogNytwatchWriter, Warning,
            TEXT("[NytwatchAgent] Could not reopen session file to backfill header: %s"),
            *SessionFilePath);
    }

    // --- Delete lock file ---------------------------------------------------
    const FString LockPath = FPaths::Combine(
        ProjectDir, TEXT("Saved"), TEXT("Nytwatch"), TEXT("nytwatch.lock"));
    IFileManager::Get().Delete(*LockPath, false, true);

    UE_LOG(LogNytwatchWriter, Log,
        TEXT("[NytwatchAgent] Session closed. %d events written to %s"),
        TotalEventCount, *FPaths::GetCleanFilename(SessionFilePath));

    bIsOpen = false;
}
