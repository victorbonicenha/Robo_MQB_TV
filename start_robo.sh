#!/bin/bash
echo "[$(date '+%H:%M:%S')] Iniciando script de boot do robô"

for i in $(seq 1 30); do
    XFCE_PID=$(pgrep -u rpa_robo xfce4-session | tail -1)
    if [ -n "$XFCE_PID" ]; then
        DISPLAY=$(cat /proc/$XFCE_PID/environ | tr '\0' '\n' | grep "^DISPLAY=" | cut -d= -f2)
        if [ -n "$DISPLAY" ]; then
            echo "[$(date '+%H:%M:%S')] DISPLAY encontrado: $DISPLAY"
            break
        fi
    fi
    echo "[$(date '+%H:%M:%S')] Aguardando sessão xfce4... tentativa $i/30"
    sleep 10
done

if [ -z "$DISPLAY" ]; then
    echo "[$(date '+%H:%M:%S')] DISPLAY não encontrado após 5 minutos, abortando"
    exit 1
fi

export DISPLAY=$DISPLAY
export XAUTHORITY=/home/rpa_robo/.Xauthority

echo "[$(date '+%H:%M:%S')] Iniciando robô com DISPLAY=$DISPLAY"
cd /home/rpa_robo/Robo_OEE
source venv/bin/activate
python NovoROBO.py
