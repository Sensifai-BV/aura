# One decision of the AURA intent node: speech and/or gesture fused into an operator command,
# gated by confidence and engagement. Published on /aura/intent/event for every resolved utterance
# or gesture, including the ones that were ignored or rejected.

uint8 DECISION_ACCEPT=0    # confidence >= accept threshold: command executes
uint8 DECISION_CONFIRM=1   # between thresholds: speaker asks the operator to confirm
uint8 DECISION_REJECT=2    # below reject threshold: dropped, operator re-prompted
uint8 DECISION_IGNORE=3    # operator not addressing the robot (engagement gate)

# stamp = time the decision was taken
std_msgs/Header header

string event_id          # unique per mission, e.g. S041-0137
string mission_id
string intent            # stop, pause, resume, confirm, cancel, next_step, target_select, come_follow, unknown
string utterance         # transcript the decision was based on ("" for gesture-only)
string gesture           # fist_raise, arm_wave, point, thumb_up, raise_wave, or ""
string modality          # voice, gesture or fused
uint8 decision

float32 confidence       # fused confidence 0..1
float32 accept_threshold # thresholds in force for this operator / intent
float32 reject_threshold
float32 latency_ms       # end of utterance/gesture -> decision
float32 noise_db         # acoustic floor at decision time, dB(A)
string acoustic_condition # clean, noise, mask, noise+mask
string operator_id
bool engaged             # operator was addressing the robot
bool safety_critical     # stop / pause / cancel

# Ground truth from operator feedback (cancel / confirm / supervisor label); outcome is
# correct, recovered, misrecognized, false_activation or rejected.
bool correct
string outcome
float32 time_to_safe_stop_ms  # stop/pause executed: decision -> robot at rest, else 0
float32 recovery_time_s       # outcome == recovered: misunderstanding -> correct command, else 0
