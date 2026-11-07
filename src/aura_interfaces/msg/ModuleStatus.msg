# Health and rates of one AURA module, published at 1 Hz on /aura/<module>/status
# (<module> = stt, intent, gesture, speaker).
#
# A flat message on purpose: Orion-LD's DDS bridge cannot read sequences of structs
# (diagnostic_msgs/DiagnosticStatus.values), so metrics are parallel string arrays.
# Common keys: rate, rate_unit ("Hz" for streams, "/min" for events), latency_ms.

uint8 OK=0
uint8 WARN=1
uint8 ERROR=2

std_msgs/Header header
uint8 level
string name
string message
string hardware_id
string[] keys      # metric names
string[] values    # metric values as strings, same length and order as keys
