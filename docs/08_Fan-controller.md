
# 08 · Configuration et automatisation du ventilateur 


**Ajouter dans /boot/firmware/config.txt :**

```bash
# Fan PWM
dtparam=cooling_fan=on

# 20% ...
dtparam=fan_temp0=55000
dtparam=fan_temp0_hyst=2500
dtparam=fan_temp0_speed=50

# 40% ...
dtparam=fan_temp1=60000
dtparam=fan_temp1_hyst=2500
dtparam=fan_temp1_speed=100

# 60% ...
dtparam=fan_temp2=65000
dtparam=fan_temp2_hyst=2500
dtparam=fan_temp2_speed=150

# 80% ...
dtparam=fan_temp3=70000
dtparam=fan_temp3_hyst=2500
dtparam=fan_temp3_speed=200

# 100% ...
dtparam=fan_temp4=75000
dtparam=fan_temp4_hyst=2500
dtparam=fan_temp4_speed=255

```