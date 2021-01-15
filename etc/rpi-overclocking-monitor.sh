#!/usr/bin/env bash

while true; do
	echo $(date) - $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq) Hz - $(/opt/vc/bin/vcgencmd measure_temp)
	sleep 1
done	
