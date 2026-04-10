import requests
from .zoho_auth import zoho_headers

#roles
def list_contact_roles(api_domain: str) -> dict:
    # api_domain p.ej. https://www.zohoapis.com
    url = f"{api_domain}/crm/v8/Contacts/roles"
    r = requests.get(url, headers=zoho_headers(), timeout=20)
    r.raise_for_status()
    return r.json()
