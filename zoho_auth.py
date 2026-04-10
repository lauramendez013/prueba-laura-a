import os, time, requests

_CACHE = {"token": None, "exp": 0}

def get_access_token() -> str:
    now = time.time()
    if _CACHE["token"] and (_CACHE["exp"] - now) > 60:
        return _CACHE["token"]

    data = {
        "refresh_token": os.environ["ZOHO_REFRESH_TOKEN"],
        "client_id": os.environ["ZOHO_CLIENT_ID"],
        "client_secret": os.environ["ZOHO_CLIENT_SECRET"],
        "grant_type": "refresh_token",
    }
    base = os.environ.get("ZOHO_ACCOUNTS_BASE", "https://accounts.zoho.com")
    url = f"{base}/oauth/v2/token"
    r = requests.post(url, data=data, timeout=20)
    r.raise_for_status()
    js = r.json()
    token = js["access_token"]
    _CACHE["token"] = token
    _CACHE["exp"] = now + int(js.get("expires_in", 3600))
    return token

def zoho_headers():
    # Zoho usa este esquema en el header Authorization_
    return {"Authorization": f"Zoho-oauthtoken {get_access_token()}"}
