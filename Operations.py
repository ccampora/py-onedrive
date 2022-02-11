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
from Item import get_etag_from_local, is_excluded
from Globals import ONEDRIVE_DB_FOLDER, ONEDRIVE_ROOT
from Globals import LOGGER as logger


def get_drive_information():
    url = "https://graph.microsoft.com/v1.0/me/drives"

    auth_header = get_bearer_auth_header()

    logger.debug("Authentication header: %s", auth_header)
    r = requests.get(url, headers=auth_header)
    jsonResponse = r.json()

    logger.debug("Authentication response: %s", pretty_json(jsonResponse))


def sync_onedrive_to_disk(onedrive_root_folder, local_path, next_link=None):

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

        if (
            "parentReference" in item
            and "path" in item["parentReference"]
            and is_excluded(f'{item["parentReference"]["path"]}/{item["name"]}')
        ):
            logger.debug(
                f'Excluding {item["parentReference"]["path"].split(":")[1]}/{item["name"]}'
            )
            continue
        
        # If root item then exclude
        if "root" in item:
            logger.debug("Skiping root item")
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
                    item["@microsoft.graph.downloadUrl"],
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

    auth_header = get_bearer_auth_header()
    r = requests.get(url, headers=auth_header)

    with open(file_full_path, "wb") as f:
        f.write(r.content)

    """
    Returns the item DB content as json
    """


def get_item_db_content(id):
    item_db_file = f"{ONEDRIVE_DB_FOLDER}/{id}"

    if os.path.exists(item_db_file):
        with open(item_db_file, "r") as file:
            return json.load(file)
    else:
        logger.warn(f"Db file for {id} not found! Cant delete file")
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
        logger.warn(f"Cannot delete item with id {item_id}")
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
        logger.warn(f"The item {item_id} with path {item_path_on_disk} does not exist")

    return True
