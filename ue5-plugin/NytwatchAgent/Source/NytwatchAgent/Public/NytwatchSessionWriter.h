#pragma once

#include "CoreMinimal.h"
#include "HAL/Runnable.h"
#include "HAL/ThreadSafeBool.h"
#include "Containers/Queue.h"
#include "NytwatchPropertyTracker.h"
#include "NytwatchConfig.h"

class FRunnableThread;
class FEvent;

// ---------------------------------------------------------------------------
// Writes a PIE session to a .md file on a dedicated background thread.
//
// Threading model
// ───────────────
// Game thread  — calls Open(), AppendEvent(), Close().
//   AppendEvent() only enqueues the event and increments the counter; it
//   does no formatting or I/O.
//
// Writer thread — owns all formatting (BuildFlushBlock) and file I/O
//   (InternalFlush).  It sleeps on a FEvent and wakes either when the game
//   thread signals new work or when the 200 ms timeout expires.
//
// The only shared state that crosses threads is:
//   • EventQueue  (TQueue SPSC — lock-free by design)
//   • WorkSignal  (FEvent  — designed for cross-thread use)
//   • bStopRequested (FThreadSafeBool — written by game thread, read by
//                     writer thread)
//
// All other members (session metadata, TotalEventCount, bCapReached, bIsOpen)
// are exclusively touched on the game thread.
// ---------------------------------------------------------------------------
class FNytwatchSessionWriter : public FRunnable
{
public:
    // Creates the session file and starts the background writer thread.
    void Open(const FNytwatchConfig& Config, const FString& ProjectDir);

    // Enqueues one event and wakes the writer thread.
    // No-ops when the cap has been reached or the writer is not open.
    void AppendEvent(const FNytwatchEvent& Event);

    // Signals the writer thread to flush remaining events and stop,
    // waits for it to finish, then backfills the header and deletes
    // the lock file.
    void Close(const FString& ProjectDir);

    // Best-effort close for crash / hard-stop scenarios (called from the
    // system-error handler).  Signals the writer thread to stop, then
    // immediately backfills the header with crash metadata without waiting
    // for the thread — the thread only appends to the end of the file, so
    // there is no race with the header region.  The lock file is intentionally
    // left on disk so external tools can detect the abnormal exit.
    void EmergencyClose(const FString& ProjectDir);

    bool    IsCapReached()       const { return bCapReached;        }
    bool    IsOpen()             const { return bIsOpen;            }
    FString GetSessionId()       const { return SessionId;          }
    int32   GetTotalEventCount() const { return TotalEventCount;    }

    // FRunnable — executed on the writer thread; do not call directly.
    virtual uint32 Run()  override;
    virtual void   Stop() override;

private:
    static constexpr int32 EventCap = 50000;

    // ── Session metadata (game thread only) ─────────────────────────────────
    FString   SessionId;
    FString   SessionFilePath;
    FDateTime StartedAtDT;
    FString   StartedAtStr;
    FString   UEProjectName;
    FString   SystemsTrackedJson;
    int32     TotalEventCount = 0;
    bool      bCapReached     = false;
    bool      bIsOpen         = false;

    // ── Cross-thread pipeline ────────────────────────────────────────────────
    TQueue<FNytwatchEvent, EQueueMode::Spsc> EventQueue;
    FEvent*          WorkSignal     = nullptr;
    FRunnableThread* WriterThread   = nullptr;
    FThreadSafeBool  bStopRequested;   // written: game thread  read: writer thread

    // ── Writer-thread accumulator (writer thread only after Open()) ─────────
    // Events are accumulated here during the session and flushed once at Close.
    TArray<FNytwatchEvent> AccumulatedEvents;

    // ── Writer-thread helpers ────────────────────────────────────────────────
    void    InternalFlush(const TArray<FNytwatchEvent>& Events);
    FString BuildFlushBlock(const TArray<FNytwatchEvent>& Events) const;

    static FString StripUEPrefix(const FString& Name);
    static FString TrimFloat(double Val, int32 Precision = 6);
    static FString FormatBool(const FString& Val);
};
