# Inspection payload readings on /artaban/environment (gas detector, thermal camera, weather).

std_msgs/Header header

float32 h2s_ppm
float32 ch4_lel           # % of lower explosive limit
float32 co_ppm
float32 o2_vol            # % volume
float32 temperature       # ambient, degC
float32 relative_humidity # %
float32 thermal_max       # hottest point in the thermal image, degC
float32 la_eq             # sound level, dB(A)
float32 la_max
string noise_source       # ambient, compressor, pump, radio, alarm
