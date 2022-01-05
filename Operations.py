import json
import os
import requests
from requests.api import request
from Authentication import get_bearer_auth_header
from Utils import pretty_json, get_folder_from_path
from Config import get_deltalink_from_db, save_deltalink_to_db, save_item_remoteinfo_to_db
from Item import get_etag_from_local
from Globals import ONEDRIVE_DB_FOLDER, ONEDRIVE_ROOT
from Config import EXCLUDE_LIST


def get_drive_information():
    url = "https://graph.microsoft.com/v1.0/me/drives"

    auth_header = get_bearer_auth_header()

    print(auth_header)
    r = requests.get(url, headers=auth_header)
    jsonResponse = r.json()

    print(pretty_json(jsonResponse))


def sync_onedrive_to_disk(onedrive_root_folder, local_path, next_link=None):

    delta_link = get_deltalink_from_db()
    if delta_link != "":
        url = delta_link
    elif next_link is not None:
        url = next_link
    else:
        url = "https://graph.microsoft.com/v1.0/me/drive/root/delta"

    print(f"Calling {url}")

    auth_header = get_bearer_auth_header()
    r = requests.get(url, headers=auth_header)
    jsonResponse = r.json()
    
    items_list = jsonResponse["value"]

    for item in items_list:
        # print(pretty_json(item))

        if "parentReference" in item and "path" in item["parentReference"] and is_excluded(item["parentReference"]["path"]):
            print(
                f'Excluding {item["parentReference"]["path"].split(":")[1]}/{item["name"]}')
            continue

        if "folder" in item:
            # print("Folder " + item["name"] + " " + item["id"] + " " + item["parentReference"]["path"].split(":")[1])
            if "parentReference" in item and "path" in item["parentReference"]:
                
                # Skip root item
                if "name" in item["parentReference"] and item["parentReference"]["name"] == "root":
                    continue
                
                sync_onedrive_to_disk_folder(
                    item["name"], item["parentReference"]["path"].split(":")[1])
        if "package" in item:
            if "type" in item["package"] and item["package"]["type"] == "oneNote":
                sync_onedrive_to_disk_folder(
                    item["name"], item["parentReference"]["path"].split(":")[1])

        if "file" in item and "deleted" not in item:
            # Skip download - no changes from last sync
            if item["eTag"] == get_etag_from_local(item["id"]):
                print(f'Skiping file {item["name"]} with id {item["id"]}')
            else:  # Download and replace existing
                sync_onedrive_to_disk_file(item["name"], item["parentReference"]["path"].split(
                    ":")[1], item["@microsoft.graph.downloadUrl"])

        if "deleted" in item:
            if delete_item_from_disk(item["id"]) is False:
                items_list.append(item)
        else:
            save_item_remoteinfo_to_db(item["id"], item)

    if "@odata.nextLink" in jsonResponse:
        sync_onedrive_to_disk(onedrive_root_folder, local_path,
                              next_link=jsonResponse["@odata.nextLink"])

    if "@odata.deltaLink" in jsonResponse:
        save_deltalink_to_db(deltaToken=jsonResponse["@odata.deltaLink"])


def sync_onedrive_to_disk_folder(folder_name, path):
    folder_full_path = f'{ONEDRIVE_ROOT}{path}/{folder_name}'

    print(f'Creating folder {folder_name} in {path}')
    if os.path.exists(folder_full_path) is False:
        os.mkdir(folder_full_path)


def sync_onedrive_to_disk_file(file_name, path, url):
    
    print(f'Getting file {file_name} from {path}')
    file_full_path = f'{ONEDRIVE_ROOT}{path}/{file_name}'

    auth_header = get_bearer_auth_header()
    r = requests.get(url, headers=auth_header)

    with open(file_full_path, 'wb') as f:
        f.write(r.content)

    """
    Returns the item DB content as json
    """


def get_item_db_content(id):
    item_db_file = f'{ONEDRIVE_DB_FOLDER}/{id}'

    if os.path.exists(item_db_file):    
        with open(item_db_file, "r") as file:
            return json.load(file)
    else:
        print(f'Db file for {id} not found! Cant delete file')
        return ""

    """
    Deletes the item db entry
    """
def delete_item_db_entry(id):
    item_db_file = f'{ONEDRIVE_DB_FOLDER}/{id}'
    
    if os.path.exists(item_db_file):
        os.remove(item_db_file)

    """
    Deletes an item from disk. It also deletes the corresponding DB entry
    """
def delete_item_from_disk(item_id):
    
    item_db_content = get_item_db_content(id=item_id)

    if item_db_content == "":
        print(f'Cannot delete item with id {item_id}')
        return
    
    item_folder = get_folder_from_path(item_db_content["parentReference"]["path"])
    item_name = item_db_content["name"]
    
    
    item_path_on_disk = f'{ONEDRIVE_ROOT}{item_folder}/{item_name}'
        
    if os.path.exists(item_path_on_disk):
        print(f'Deleting {item_path_on_disk}')
        
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
        print(f'The item {item_id} with path {item_path_on_disk} does not exist')
        
    return True

def is_excluded(path):
    for e in EXCLUDE_LIST:
        if path.find(e["path"]) != -1:
            return True
    return False