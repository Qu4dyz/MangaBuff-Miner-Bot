# Cup tactical corrections

The previous one-hit ranking could mistake barrier/immortality damage for a kill, choose a lethal thorns trade, or heal the largest HP deficit rather than save an endangered attacker.

The fast policy includes damage spread, crit/dodge chances, barrier and immortality state, reflected damage, and the reduction of incoming kill risk after healing. Existing taunt restrictions, used-card constraints, secret placement, server acknowledgements and revision checks remain in force. Unknown history/effects keep the previous action. No rollout planner is included in this release.

The version tag cup-tactics-v2 is stored with Cup decisions and final records. Final server states and available cumulative events are archived without claiming traces are complete.

Validation: regression tests cover each failure, history gaps, used cards, taunt, malformed optional history and input immutability. On the existing 382-position diagnostic set, two choices change and no illegal actions appear. This is not a measured live win-rate improvement.

Rollback: switch production to its saved predeployment revision, restart mangabuff.service, verify active process/logs. Data/settings are not part of this source update.
