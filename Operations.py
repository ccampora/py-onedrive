from Config import get_deltalink_from_db, save_deltalink_to_db
from GraphAPI import (
    get as graph_get,
    put_bytes,
    post_json,
    patch_json,
    delete_item as graph_delete,
    GraphAPIError,
)
from Globals import LOGGER as logger

DELTA_URL = "https://graph.microsoft.com/v1.0/me/drive/root/delta"
_GRAPH = "https://graph.microsoft.com/v1.0/me/drive"


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
    Raises FileNotFoundError on 404, IOError on other failures.
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


def upload_new_file(parent_id, name, content):
    """Upload content as a new file. Returns the OneDrive item dict."""
    url = f"{_GRAPH}/items/{parent_id}:/{name}:/content"
    try:
        return put_bytes(url, content)
    except GraphAPIError as e:
        raise IOError(f"upload_new_file failed: {e}")


def overwrite_file(item_id, content):
    """Replace content of an existing file. Returns the updated item dict."""
    url = f"{_GRAPH}/items/{item_id}/content"
    try:
        return put_bytes(url, content)
    except GraphAPIError as e:
        raise IOError(f"overwrite_file failed: {e}")


def create_folder(parent_id, name):
    """
    Create a new folder. Returns the item dict.
    Raises FileExistsError on 409, IOError on other failures.
    """
    url = f"{_GRAPH}/items/{parent_id}/children"
    payload = {
        "name": name,
        "folder": {},
        "@microsoft.graph.conflictBehavior": "fail",
    }
    try:
        return post_json(url, payload)
    except GraphAPIError as e:
        if e.status_code == 409:
            raise FileExistsError(f"'{name}' already exists")
        raise IOError(f"create_folder failed: {e}")


def delete_remote_item(item_id):
    """Delete an item from OneDrive. Treats 404 as success."""
    url = f"{_GRAPH}/items/{item_id}"
    try:
        graph_delete(url)
    except GraphAPIError as e:
        if e.status_code == 404:
            return
        raise IOError(f"delete_remote_item failed: {e}")


def move_rename_item(item_id, new_name=None, new_parent_id=None):
    """Rename and/or move an item. Returns the updated item dict."""
    url = f"{_GRAPH}/items/{item_id}"
    payload = {}
    if new_name:
        payload["name"] = new_name
    if new_parent_id:
        payload["parentReference"] = {"id": new_parent_id}
    try:
        return patch_json(url, payload)
    except GraphAPIError as e:
        raise IOError(f"move_rename_item failed: {e}")
