import webbrowser
import requests

from Config import get_current_bearer
from Config import get_current_refresh_token
from Config import save_bearer_response

# AUTH URL https://login.microsoftonline.com/common/oauth2/v2.0/authorize?client_id=22c49a0d-d21c-4792-aed1-8f163c982546&scope=Files.ReadWrite%20Files.ReadWrite.all%20Sites.ReadWrite.All%20offline_access&response_type=code&redirect_uri=https://login.microsoftonline.com/common/oauth2/nativeclient

AUTH_BASE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?"
AUTH_CLIENT_ID = "22c49a0d-d21c-4792-aed1-8f163c982546"
AUTH_SCOPE = "Files.ReadWrite%20Files.ReadWrite.all%20Sites.ReadWrite.All%20offline_access"
AUTH_RESPONSE_TYPE = "code"
AUTH_REDIRECT_URI = "https://login.microsoftonline.com/common/oauth2/nativeclient"

CODE_BASE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
CODE_GRANT_TYPE = "authorization_code"

# Microsoft docs: https://docs.microsoft.com/en-us/onedrive/developer/rest-api/getting-started/graph-oauth?view=odsp-graph-online#step-1-get-an-authorization-code
def get_auth_url():
    return f'{AUTH_BASE_URL}client_id={AUTH_CLIENT_ID}&scope={AUTH_SCOPE}&response_type={AUTH_RESPONSE_TYPE}&redirect_uri={AUTH_REDIRECT_URI}'

def open_auth_page():
    webbrowser.open_new(
        get_auth_url()
    )
    return input("Enter the URL with the code")

# Microsoft docs: https://docs.microsoft.com/en-us/onedrive/developer/rest-api/getting-started/graph-oauth?view=odsp-graph-online#step-2-redeem-the-code-for-access-tokens
def get_bearer_token(url_with_code):
    url = f'{CODE_BASE_URL}'
    url_body = {"code": url_with_code,
                "client_id": AUTH_CLIENT_ID,
                "redirect_uri": AUTH_REDIRECT_URI,
                "grant_type": CODE_GRANT_TYPE}

    r = requests.post(url, data=url_body)
    
    jsonResponse = r.json()
    for key, value in jsonResponse.items():
        print(key, ":", value)
    return jsonResponse


# Microsoft docs: https://docs.microsoft.com/en-us/onedrive/developer/rest-api/getting-started/graph-oauth?view=odsp-graph-online#step-3-get-a-new-access-token-or-refresh-token
def get_refresh_token():
    
    current_refresh_token = get_current_refresh_token()
    
    refresh_url = f'{CODE_BASE_URL}'
    refresh_body = {
        "client_id": AUTH_CLIENT_ID,
        "redirect_uri": AUTH_REDIRECT_URI,
        "refresh_token": current_refresh_token,
        "grant_type": "refresh_token"
    }
    
    r = requests.post(refresh_url, data=refresh_body)
    jsonResponse = r.json()
    
    for key, value in jsonResponse.items():
        print(key, ":", value)
    return jsonResponse
    
def print_current_bearer():
    print(get_current_bearer())

def get_bearer_auth_header():
    current_bearer_token = get_current_bearer()
    return { "Authorization": f'Bearer {current_bearer_token}'}

# Runs the auth flow. If no bearer token is found, it creates one, or refresh if expired.
# return:
# True is a new Bearer was acquired or the current one is valid
# False if it was no possible to acquire a new Bearer

def authenticate():
    
    current_bearer_token = get_current_bearer()
    
    if current_bearer_token is None:
        code = open_auth_page()
        bearer_response = get_bearer_token(code)
        save_bearer_response(bearer_response)
    
    refresh_response = get_refresh_token()
    save_bearer_response(refresh_response)
