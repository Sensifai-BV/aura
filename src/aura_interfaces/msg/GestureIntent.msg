# One gesture decision from aura_gesture.
# Published for every processed frame on the hypothesis topic and, only when a
# gesture is accepted, on the intent topic.

uint8 STATE_IDLE=0       # no actionable gesture in the voting window
uint8 STATE_CANDIDATE=1  # gesture seen, not (yet) accepted: below votes, cooling down or disarmed
uint8 STATE_ACCEPTED=2   # accepted intent; emitted once per held gesture

# stamp = capture time of the frame that produced this decision
std_msgs/Header header

string gesture           # canonical: thumb_up, thumb_down, fist, palm, point, none
string intent            # mapped operator intent: confirm, reject, stop, cancel, select, none
string raw_label         # detector class name of the best hand in this frame
uint8 state

float32 confidence       # mean score of `gesture` over the voting window, 0..1
float32 frame_confidence # detector score of `gesture` in this frame, 0..1
float32 threshold        # acceptance threshold applied to `gesture`
uint16 votes             # frames in the window at or above threshold
uint16 window            # voting window length in frames

vision_msgs/BoundingBox2D bbox  # best hand in this frame, image pixels

float32 inference_ms     # model time for this frame
float32 latency_ms       # capture stamp -> publish
string model_id          # backend that produced it (A/B attribution)
