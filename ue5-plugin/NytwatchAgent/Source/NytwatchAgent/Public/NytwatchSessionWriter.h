#pragma once

#include "CoreMinimal.h"
#include "NytwatchPropertyTracker.h"
#include "NytwatchConfig.h"

// ---------------------------------------------------------------------------
// Buffers events during a PIE session, flushes to disk periodically, and
// finalises the session markdown file when PIE ends.
//
// Memory is bounded: at most FlushThreshold events are held in memory at any
// one time.  When TotalEventCount reaches EventCap, recording stops and an
// in-editor notification is shown.
// ---------------------------------------------------------------------------
class FNytwatchSessionWriter
{
public:
    // Creates dirs, writes header + legend, records start time.
    void Open(const FNytwatchConfig& Config, const FString& ProjectDir);

    // Buffers one event.  Triggers a flush when the buffer hits FlushThreshold.
    // No-ops if the event cap has already been reached or the writer is closed.
    void AppendEvent(const FNytwatchEvent& Event);

    // Serialises the current buffer to the session file (append) and clears it.
    void FlushBuffer();

    // Flushes remaining events, backfills header placeholders, deletes lock file.
    void Close(const FString& ProjectDir);

    bool   IsCapReached()      const { return bCapReached; }
    bool   IsOpen()            const { return bIsOpen; }
    FString GetSessionId()     const { return SessionId; }
    int32  GetTotalEventCount() const { return TotalEventCount; }

private:
    static constexpr int32 FlushThreshold = 10000;
    static constexpr int32 EventCap       = 50000;

    TArray<FNytwatchEvent> Buffer;

    FString   SessionId;
    FString   SessionFilePath;
    FDateTime StartedAtDT;
    FString   StartedAtStr;   // ISO string written to header
    FString   UEProjectName;
    FString   SystemsTrackedJson;

    int32 TotalEventCount = 0;
    bool  bCapReached     = false;
    bool  bIsOpen         = false;

    // --- formatting helpers -------------------------------------------------
    static FString StripUEPrefix(const FString& Name);
    static FString TrimFloat(double Val);
    static FString FormatBool(const FString& Val);

    // Serialise the current buffer into a markdown string ready for appending.
    FString BuildFlushBlock() const;
};
