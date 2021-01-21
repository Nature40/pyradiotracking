#!/usr/bin/env bash

while true; do
	temp=$(cat /sys/class/thermal/thermal_zone0/temp)

	echo $(date) - $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq) Hz - ${temp:0:2}.${temp:2} °C
	sleep 1
done