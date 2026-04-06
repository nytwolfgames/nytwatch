#pragma once

#include "CoreMinimal.h"
#include "NytwatchPropertyTracker.h"
#include "NytwatchConfig.h"

class IWebSocket;

// ---------------------------------------------------------------------------
// Streams a PIE session to the Nytwatch server over WebSocket.
//
// Threading model
// ───────────────
// All methods must be called on the game thread.  WebSocket callbacks from
// IWebSocket are also delivered on the game thread in UE5, so no locking is
// needed.
//
// Lifecycle
// ─────────
//   Open()        — generate session ID, connect WebSocket.
//   (async)       — OnConnected fires → send session_open, set bReady.
//   SendBatch()   — called each tick; no-op until bReady is true.
//   Close()       — send session_close "normal"; set bPendingDisconnect.
//   FlushAndDisconnect() — close+reset the socket (call ~100 ms after Close).
//   EmergencyClose() — best-effort send of session_close "crash" + immediate
//                      socket close (crash-handler context).
//
// Reconnect
// ─────────
// If the WebSocket closes unexpectedly during a session, bNeedsReconnect is
// set to true.  The subsystem checks NeedsReconnect() on each tick and calls
// TryReconnect() to re-establish the connection.  Events produced while
// disconnected are discarded.
// ---------------------------------------------------------------------------
class FNytwatchSessionWriter
{
public:
    // Generates a new session ID and initiates the WebSocket connection.
    // Returns immediately; bReady becomes true only after OnConnected fires
    // and the session_open message has been sent.
    void Open(const FNytwatchConfig& Config);

    // Send all events produced in one tick as a single batch message.
    // No-op if bReady is false (still connecting) or bCapReached is true.
    void SendBatch(const TArray<FNytwatchEvent>& Events, float PIEElapsedSeconds);

    // Send session_close with end_reason "normal".
    // Does NOT close the WebSocket immediately — call FlushAndDisconnect()
    // ~100 ms later so the send buffer can drain before the close frame fires.
    void Close(float PIEElapsedSeconds);

    // Actually close and release the WebSocket.  Must be called after Close().
    // Safe to call even if Close() was never reached (e.g. already reset).
    void FlushAndDisconnect();

    // Best-effort close for crash / OnHandleSystemError contexts.
    // Sends session_close with end_reason "crash" and closes the socket
    // without waiting for confirmation.
    void EmergencyClose(float PIEElapsedSeconds);

    // Called by the subsystem tick when NeedsReconnect() is true.
    // Creates a fresh WebSocket and re-connects using the stored URL.
    void TryReconnect();

    // Cleanly abort a pending connection attempt (e.g. on connection timeout).
    void Abort();

    bool    IsOpen()              const { return bIsOpen;            }
    bool    IsReady()             const { return bReady;             }
    bool    IsCapReached()        const { return bCapReached;        }
    bool    NeedsReconnect()      const { return bNeedsReconnect;    }
    bool    IsPendingDisconnect() const { return bPendingDisconnect; }
    FString GetSessionId()    const { return SessionId;        }
    int32   GetTotalEventCount() const { return TotalEventCount; }

private:
    // ── WebSocket callbacks (all on game thread) ─────────────────────────────
    void OnConnected();
    void OnConnectionError(const FString& Error);
    void OnClosed(int32 StatusCode, const FString& Reason, bool bWasClean);
    void OnMessage(const FString& Msg);  // not used; required to bind

    // ── Message builders ─────────────────────────────────────────────────────
    void    SendSessionOpen();
    void    SendSessionClose(float DurationSeconds, const FString& EndReason);
    FString BuildBatchJson(const TArray<FNytwatchEvent>& Events, float T) const;

    static FString EscapeJsonString(const FString& Str);

    // ── WebSocket ────────────────────────────────────────────────────────────
    void CreateAndConnect();

    TSharedPtr<IWebSocket> WebSocket;
    FString WebSocketUrl;

    // ── Session metadata (set in Open, stable for session lifetime) ──────────
    FString   SessionId;
    FString   StartedAtStr;
    FDateTime StartedAtDT;
    FString   UEProjectName;
    FString   ArmedSystemsJson; // JSON array of system name strings

    // ── Counters / flags ─────────────────────────────────────────────────────
    int32 TotalEventCount      = 0;
    bool  bIsOpen              = false; // true between Open() and Close/EmergencyClose
    bool  bReady               = false; // true after WS connected + session_open sent
    bool  bCapReached          = false;
    bool  bNeedsReconnect      = false;
    bool  bPendingDisconnect   = false; // set by Close(); cleared by FlushAndDisconnect()
    int32 ReconnectAttempts    = 0;

    static constexpr int32 EventCap             = 50000;
    static constexpr int32 MaxReconnectAttempts = 5;
};
