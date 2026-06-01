import json
import os
import requests
from requests.api import request
from Authentication import get_bearer_auth_header
from Utils import pretty_json, get_folder_from_path
from Config import (
    get_deltalink_from_db,
    save_deltalink_to_db,
    save_item_remoteinfo_to_db,
)
from Item import get_etag_from_local, is_excluded, is_included, should_download_simple
from GraphAPI import get as graph_get, GraphAPIError
from Globals import ONEDRIVE_DB_FOLDER, ONEDRIVE_ROOT
from Globals import LOGGER as logger

DELTA_URL = "https://graph.microsoft.com/v1.0/me/drive/root/delta"


def get_drive_information():
    url = "https://graph.microsoft.com/v1.0/me/drives"

    auth_header = get_bearer_auth_header()

    logger.debug("Authentication header: %s", auth_header)
    r = requests.get(url, headers=auth_header)
    jsonResponse = r.json()

    logger.debug("Authentication response: %s", pretty_json(jsonResponse))


def sync_onedrive_to_disk(onedrive_root_folder, local_path, next_link=None):

    # Check if the onedrive root folder exists, if not create it
    if not os.path.exists(ONEDRIVE_ROOT):
        logger.info(f"Creating onedrive root folder at {ONEDRIVE_ROOT}")
        os.makedirs(ONEDRIVE_ROOT, exist_ok=True)

    delta_link = get_deltalink_from_db()
    if next_link is not None:
        url = next_link
    elif delta_link != "":
        url = delta_link
    else:
        url = "https://graph.microsoft.com/v1.0/me/drive/root/delta"

    logger.debug(f"Calling {url}")

    auth_header = get_bearer_auth_header()
    r = requests.get(url, headers=auth_header)
    jsonResponse = r.json()

    items_list = jsonResponse["value"]

    pending_folder_deletion = []

    if len(items_list) == 0:
        logger.info("Nothing to sync")
        return 
    
    for item in items_list:
        logger.debug("Processing item content: %s", pretty_json(item))

        if (item["name"] == "root"):
            continue
        
        if not should_download_simple(item):
            continue
         
        if "folder" in item:
            if "parentReference" in item and "path" in item["parentReference"]:

                # Skip root item
                if (
                    "name" in item["parentReference"]
                    and item["parentReference"]["name"] == "root"
                ):
                    continue

                sync_onedrive_to_disk_folder(
                    item["name"], item["parentReference"]["path"].split(":")[1]
                )
        if "package" in item:
            if "type" in item["package"] and item["package"]["type"] == "oneNote":
                sync_onedrive_to_disk_folder(
                    item["name"], item["parentReference"]["path"].split(":")[1]
                )

        if "file" in item and "deleted" not in item:
            # Skip download - no changes from last sync
            if item["eTag"] == get_etag_from_local(item["id"]):
                logger.debug(f'Skiping file {item["name"]} with id {item["id"]}')
            else:  # Download and replace existing
                sync_onedrive_to_disk_file(
                    item["name"],
                    item["parentReference"]["path"].split(":")[1],
                    #item["@microsoft.graph.downloadUrl"],
                    #item["webUrl"],
                    f'https://graph.microsoft.com/v1.0/me/drive/items/{item["id"]}/content',
                )

        if "deleted" in item:
            # If the directory is not empty, then is added to the second queue in reverse order.
            # When all the items in the first queue are proccesed, all the files should have been deleted.
            # Folders cannot be deleted unless they are empty, hence those items are added to a second queue in reverse order.
            # The second queue will be processed after the first queue is done.
            # Issue: #5
            if delete_item_from_disk(item["id"]) is False:
                pending_folder_deletion.insert(0, item)
                # items_list.append(item)
        else:
            save_item_remoteinfo_to_db(item["id"], item)

    if "@odata.nextLink" in jsonResponse:
        sync_onedrive_to_disk(
            onedrive_root_folder, local_path, next_link=jsonResponse["@odata.nextLink"]
        )

    if "@odata.deltaLink" in jsonResponse:
        save_deltalink_to_db(deltaToken=jsonResponse["@odata.deltaLink"])

    # Deletes pending folders. By this point all folders should be empty, if the count files of any is not 0 this
    # means some files were not deleted and the folder can't be deleted. This should trow some kind of exception.
    # Issue: #5
    # TODO: Log exception

    for folder in pending_folder_deletion:
        if delete_item_from_disk(folder["id"]) is False:
            # TODO: Throw exception or log
            continue


def sync_onedrive_to_disk_folder(folder_name, path):
    folder_full_path = f"{ONEDRIVE_ROOT}{path}/{folder_name}"

    if path == "":
        logger.info(f"Creating folder {folder_name} in /")
    else:
        logger.info(f"Creating folder {folder_name} in {path}")
    if os.path.exists(folder_full_path) is False:
        os.mkdir(folder_full_path)


def sync_onedrive_to_disk_file(file_name, path, url):

    logger.info(f"Getting file {file_name} from {path}")
    file_full_path = f"{ONEDRIVE_ROOT}{path}/{file_name}"

    # Ensure parent directories exist
    parent_dir = os.path.dirname(file_full_path)
    os.makedirs(parent_dir, exist_ok=True)

    try:
        auth_header = get_bearer_auth_header()
        r = requests.get(url, headers=auth_header)

        # Check if the download was successful
        if r.status_code == 200:
            with open(file_full_path, "wb") as f:
                f.write(r.content)
            logger.info(f"Successfully downloaded: {file_full_path}")
        else:
            logger.error(f"Failed to download file {file_name}. Status: {r.status_code}")
            
    except Exception as e:
        logger.error(f"Error downloading file {file_name}: {str(e)}")


"""
Returns the item DB content as json
"""
def get_item_db_content(id):
    item_db_file = f"{ONEDRIVE_DB_FOLDER}/{id}"

    if os.path.exists(item_db_file):
        with open(item_db_file, "r") as file:
            return json.load(file)
    else:
        logger.warning(f"Db file for {id} not found! Cant delete file")
        return ""

    """
    Deletes the item db entry
    """


def delete_item_db_entry(id):
    item_db_file = f"{ONEDRIVE_DB_FOLDER}/{id}"

    if os.path.exists(item_db_file):
        os.remove(item_db_file)

    """
    Deletes an item from disk. It also deletes the corresponding DB entry
    """


def delete_item_from_disk(item_id):

    item_db_content = get_item_db_content(id=item_id)

    if item_db_content == "":
        logger.warning(f"Cannot delete item with id {item_id}")
        return

    item_folder = get_folder_from_path(item_db_content["parentReference"]["path"])
    item_name = item_db_content["name"]

    item_path_on_disk = f"{ONEDRIVE_ROOT}{item_folder}/{item_name}"

    if os.path.exists(item_path_on_disk):
        logger.info(f"Deleting {item_path_on_disk}")

        if os.path.isfile(item_path_on_disk):
            os.remove(item_path_on_disk)
        elif os.path.isdir(item_path_on_disk):
            if len(os.listdir(item_path_on_disk)) == 0:
                os.rmdir(item_path_on_disk)
            else:
                # Cannot delete folder as is not empty
                return False

        delete_item_db_entry(id=item_id)
    else:
        logger.warning(f"The item {item_id} with path {item_path_on_disk} does not exist")

    return True


# ---------------------------------------------------------------------------
# On-demand FUSE: metadata sync and content fetch
# ---------------------------------------------------------------------------

def sync_metadata(index, next_link=None):
    """
    Fetch changes from the OneDrive delta API and update the MetadataIndex.
    Does NOT download any file content.

    On the first call (empty delta link) this does a full metadata scan.
    Subsequent calls only fetch changes since the last run.

    Returns a list of item IDs that were added, changed, or deleted —
    used by the FUSE background poller to invalidate stale inodes.
    """
    delta_link = get_deltalink_from_db()

    if next_link is not None:
        url = next_link
    elif delta_link:
        url = delta_link
    else:
        url = DELTA_URL

    logger.debug(f"sync_metadata: GET {url}")

    try:
        r = graph_get(url)
    except GraphAPIError as e:
        if e.status_code == 410 and next_link is None:
            # Delta token expired — clear it and start a full resync
            logger.warning("Delta link expired (410 resyncRequired) — starting full resync")
            save_deltalink_to_db(deltaToken="")
            return sync_metadata(index)
        raise
    response = r.json()

    items = response.get("value", [])
    changed_ids = []

    if not items:
        logger.info("sync_metadata: nothing new")

    for item in items:
        if item.get("name") == "root":
            continue

        item_id = item["id"]

        if "deleted" in item:
            index.delete(item_id)
            changed_ids.append(item_id)
        else:
            index.upsert(item)
            changed_ids.append(item_id)

    if "@odata.nextLink" in response:
        changed_ids += sync_metadata(index, next_link=response["@odata.nextLink"])

    if "@odata.deltaLink" in response:
        save_deltalink_to_db(deltaToken=response["@odata.deltaLink"])

    return changed_ids


def download_file(item_id):
    """
    Fetch file content from the Graph API and return the raw bytes.
    Raises IOError on failure (after all retries are exhausted).
    """
    url = f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/content"
    try:
        r = graph_get(url)
        return r.content
    except GraphAPIError as e:
        logger.error(f"download_file: failed for {item_id} — {e}")
        if e.status_code == 404:
            raise FileNotFoundError(str(e))
        raise IOError(str(e))
