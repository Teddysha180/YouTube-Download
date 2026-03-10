# keep_alive.py (optional - run locally or on another free service)
import requests
import time
import os

RENDER_URL = "https://your-bot-name.onrender.com"  # Your Render app URL

def ping():
    while True:
        try:
            response = requests.get(RENDER_URL)
            print(f"Pinged at {time.ctime()}: {response.status_code}")
        except:
            print(f"Ping failed at {time.ctime()}")
        time.sleep(600)  # Ping every 10 minutes

if __name__ == "__main__":
    ping()
