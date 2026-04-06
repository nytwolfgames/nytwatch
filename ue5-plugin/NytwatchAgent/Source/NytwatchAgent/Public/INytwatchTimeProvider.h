#pragma once

#include "CoreMinimal.h"

// ---------------------------------------------------------------------------
// Optional interface for providing game-time strings to the Nytwatch tracker.
//
// Implement this in a game-side adapter and pass the instance to
// UNytwatchSubsystem::SetTimeProvider().  The subsystem calls
// GetCurrentTimeString() on every poll to label the output block in the
// session log.
//
// If no provider is set the subsystem falls back to wall-clock seconds.
//
// Usage
// ─────
//   class UMyAdapter : public UObject, public INytwatchTimeProvider
//   {
//       virtual FString GetCurrentTimeString() override
//       {
//           return FString::Printf(TEXT("Day %d, Year %d, Hour %d"),
//               GameState->Time.Days,
//               GameState->Time.Years,
//               GameState->Time.Hours);
//       }
//   };
//
//   // On mode start:
//   UNytwatchSubsystem::Get()->SetTimeProvider(MyAdapter);
//
//   // On mode end (before the next adapter initialises):
//   UNytwatchSubsystem::Get()->SetTimeProvider(nullptr);
//
// Multiple adapters
// ─────────────────
// Only one provider is active at a time.  When a new gameplay mode starts,
// its adapter calls SetTimeProvider(this), replacing the previous provider.
// The outgoing adapter should call SetTimeProvider(nullptr) — or check
// GetTimeProvider() == this before clearing — to avoid overwriting a
// provider set by the incoming mode.
// ---------------------------------------------------------------------------
class NYTWATCHAGENT_API INytwatchTimeProvider
{
public:
    virtual ~INytwatchTimeProvider() = default;

    // Return a human-readable string representing the current in-game time.
    // Called once per deferred poll and once per routine tick poll.
    // Must be safe to call on the game thread at any time during PIE.
    virtual FString GetCurrentTimeString() = 0;
};
