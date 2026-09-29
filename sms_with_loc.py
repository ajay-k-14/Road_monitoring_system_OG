import os
import requests
from dotenv import load_dotenv

load_dotenv()

 
def send_sos(message=None, phone_numbers=None):
    """Send an emergency SMS to the supplied recipients.

    Credentials and the gateway URL are configured through environment variables.
    Return True when SMSGate accepts the request; this does not confirm delivery.
    """
    username = os.environ.get("SMS_GATE_USERNAME")
    password = os.environ.get("SMS_GATE_PASSWORD")
    url = os.environ.get(
        "SMS_GATE_URL", "https://api.sms-gate.app/3rdparty/v1/messages"
    )
    recipients = [phone for phone in (phone_numbers or []) if phone]
    text = message or (
        "EMERGENCY SOS\n\n"
        "Accident / Hazard detected!\n"
        "Please contact the driver immediately."
    )

    if not username or not password:
        print("SMS not sent: SMS_GATE_USERNAME and SMS_GATE_PASSWORD are required")
        return False
    if not recipients:
        print("SMS not sent: no emergency contact phone numbers are configured")
        return False

    payload = {
        "textMessage": {
            "text": text
        },
        "phoneNumbers": recipients
    }

    try:
        response = requests.post(
            url,
            json=payload,
            auth=(username, password),
            headers={
                "Content-Type": "application/json"
            },
            timeout=20
        )

        print("Status:", response.status_code)
        print("Response:", response.text)

        if response.status_code == 202:
            print("SMSGate accepted the SOS SMS request; delivery is not confirmed")
            return True

        elif response.ok:
            print("SMSGate accepted the SOS SMS request; delivery is not confirmed")
            return True

        else:
            print("❌ Failed to send SOS SMS")
            return False

    except requests.exceptions.RequestException as e:

        print("❌ Internet/API error:", e)
        return False


if __name__ == "__main__":
    send_sos()