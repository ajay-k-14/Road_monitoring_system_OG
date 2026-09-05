import json
import requests


def get_windows_ip_location():
    """Fetches approximate location using IP address on Windows."""
    try:
        res = requests.get("https://ipinfo.io/json", timeout=5).json()
        loc = res.get("loc")  # returns "lat,lon"
        if loc:
            return f"https://maps.google.com/?q={loc}"
    except Exception as err:
        print(f"Location error: {err}")
    return "Location unavailable"


def send_sos():
    # --- PHONE GATEWAY CONFIGURATION ---
    # Put the exact IP, port, and credentials shown inside your phone's app
    GATEWAY_URL = "http://192.168.1.10:8080"  # Change to your phone's IP
    USERNAME = "sms"  # As set in the app
    PASSWORD = "cD0F3vaW"  # As set in the app

    TARGET_NUMBERS = ["+919496917539"]  # Target recipient
    ALERT_TEXT = "EMERGENCY SOS: Accident / Hazard detected!"

    print("Fetching location...")
    maps_link = get_windows_ip_location()
    final_message = f"{ALERT_TEXT}\nLocation:\n{maps_link}"

    payload = {"message": final_message, "phoneNumbers": TARGET_NUMBERS}

    try:
        response = requests.post(
            f"{GATEWAY_URL}/message",
            json=payload,
            auth=(USERNAME, PASSWORD),
            timeout=10,
        )

        if response.status_code in (200,201,202):
            print("SOS SMS dispatched successfully via phone gateway!")
        else:
            print(f"Failed. Status: {response.status_code}, {response.text}")
    except requests.exceptions.ConnectionError:
        print(
            "Connection failed! Make sure your PC and Android phone are on the same Wi-Fi / Hotspot."
        )


if __name__ == "__main__":
    send_sos()