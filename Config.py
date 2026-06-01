import os
import json
from Globals import CONFIG_FOLDER, SECRETS_FILE, ONEDRIVE_DB_FOLDER, DELTALINK_FILE


def create_pyonedrive_config_folder():
    if not os.path.exists(CONFIG_FOLDER):
        os.mkdir(CONFIG_FOLDER)


def init_onedrive_database():
    if not os.path.exists(ONEDRIVE_DB_FOLDER):
        os.mkdir(ONEDRIVE_DB_FOLDER)


def save_bearer_response(bearer_response):
    _ensure_secrets_file()
    with open(SECRETS_FILE, "w") as f:
        json.dump(bearer_response, f)


def get_current_bearer():
    _ensure_secrets_file()
    return _read_secrets()["access_token"]


def get_current_refresh_token():
    _ensure_secrets_file()
    return _read_secrets()["refresh_token"]


def save_deltalink_to_db(deltaToken):
    with open(DELTALINK_FILE, "w") as f:
        json.dump({"deltalink": deltaToken}, f)


def get_deltalink_from_db():
    _ensure_deltalink_file()
    with open(DELTALINK_FILE, "r") as f:
        return json.load(f)["deltalink"]


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _ensure_secrets_file():
    if not os.path.isfile(SECRETS_FILE):
        with open(SECRETS_FILE, "w") as f:
            json.dump({"access_token": ""}, f)


def _read_secrets():
    with open(SECRETS_FILE, "r") as f:
        return json.load(f)


def _ensure_deltalink_file():
    if not os.path.isfile(DELTALINK_FILE):
        with open(DELTALINK_FILE, "w") as f:
            json.dump({"deltalink": ""}, f)
