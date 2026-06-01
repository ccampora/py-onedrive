import os
import json

from Globals import ONEDRIVE_DB_FOLDER, INODE_FILE, LOGGER as logger

ROOT_INODE = 1  # matches pyfuse3.ROOT_INODE


class MetadataIndex:
    """
    In-memory index of OneDrive item metadata, backed by the existing
    file-based DB (~/.py-onedrive/db/<item_id>).

    Provides fast lookups needed by the FUSE driver:
      - by item ID         (getattr)
      - by parent + name   (lookup)
      - by parent ID       (readdir)
      - by inode           (all FUSE ops)

    Inodes are assigned on first sight and persisted in INODE_FILE so
    they survive process restarts (FUSE requires stable inodes).
    """

    def __init__(self):
        self._id_to_item = {}           # id → raw item dict from Graph API
        self._parent_to_children = {}   # parent_id → [child_id, ...]
        self._parent_name_to_id = {}    # (parent_id, name) → id
        self._id_to_inode = {}          # id → inode int
        self._inode_to_id = {}          # inode → id
        self._next_inode = 2            # 1 is reserved for ROOT_INODE
        self._root_id = None            # OneDrive root item ID (not stored in DB)

    # ------------------------------------------------------------------
    # Public: lifecycle
    # ------------------------------------------------------------------

    def load(self):
        """
        Scan the DB folder, load every item into memory, restore inode
        mapping, and discover the root item ID.
        """
        self._load_inode_mapping()

        if not os.path.exists(ONEDRIVE_DB_FOLDER):
            logger.info("DB folder does not exist yet — starting with empty index")
            return

        count = 0
        for filename in os.listdir(ONEDRIVE_DB_FOLDER):
            filepath = os.path.join(ONEDRIVE_DB_FOLDER, filename)
            if not os.path.isfile(filepath):
                continue
            try:
                with open(filepath, "r") as f:
                    item = json.load(f)
                self._index_item(item)
                count += 1
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Skipping corrupted DB file {filename}: {e}")

        self._discover_root()
        logger.info(f"Loaded {count} items into metadata index (root_id={self._root_id})")

    # ------------------------------------------------------------------
    # Public: lookups
    # ------------------------------------------------------------------

    def get_item(self, item_id):
        """Return item dict or None."""
        return self._id_to_item.get(item_id)

    def get_children(self, parent_id):
        """Return list of item dicts that are direct children of parent_id."""
        child_ids = self._parent_to_children.get(parent_id, [])
        return [self._id_to_item[cid] for cid in child_ids if cid in self._id_to_item]

    def lookup(self, parent_id, name):
        """Return item dict by (parent_id, name) or None."""
        item_id = self._parent_name_to_id.get((parent_id, name))
        return self._id_to_item.get(item_id) if item_id else None

    def get_root_id(self):
        return self._root_id

    # ------------------------------------------------------------------
    # Public: inode mapping
    # ------------------------------------------------------------------

    def get_inode(self, item_id):
        """Return the inode for item_id, assigning one if this is first sight."""
        if item_id not in self._id_to_inode:
            self._assign_inode(item_id)
            self._inode_dirty = True  # defer disk write; caller must flush
        return self._id_to_inode[item_id]

    def flush_inode_mapping(self):
        """Write inode mapping to disk if dirty. Call from a background thread."""
        if getattr(self, "_inode_dirty", False):
            self._save_inode_mapping()
            self._inode_dirty = False

    def get_id_for_inode(self, inode):
        """Return item_id for inode or None."""
        return self._inode_to_id.get(inode)

    # ------------------------------------------------------------------
    # Public: mutations (called by the metadata sync and deletion paths)
    # ------------------------------------------------------------------

    def upsert(self, item_dict):
        """
        Add or update an item in the in-memory index and persist it to DB.
        If the item already exists its old index entries are removed first
        (name or parent may have changed).
        """
        item_id = item_dict.get("id")
        if not item_id:
            logger.warning("upsert called with item missing 'id' field")
            return

        if item_id in self._id_to_item:
            self._deindex_item(item_id)

        self._index_item(item_dict)
        self._maybe_update_root(item_dict)
        self._save_item_to_db(item_id, item_dict)
        self._inode_dirty = True

    def delete(self, item_id):
        """Remove item from the in-memory index and delete its DB file."""
        self._deindex_item(item_id)
        self._delete_item_from_db(item_id)

    # ------------------------------------------------------------------
    # Private: indexing
    # ------------------------------------------------------------------

    def _index_item(self, item):
        item_id = item.get("id")
        if not item_id:
            return

        name = item.get("name", "")
        parent_id = item.get("parentReference", {}).get("id")

        self._id_to_item[item_id] = item

        if parent_id:
            self._parent_to_children.setdefault(parent_id, [])
            if item_id not in self._parent_to_children[parent_id]:
                self._parent_to_children[parent_id].append(item_id)
            self._parent_name_to_id[(parent_id, name)] = item_id

        if item_id not in self._id_to_inode:
            self._assign_inode(item_id)

    def _deindex_item(self, item_id):
        item = self._id_to_item.pop(item_id, None)
        if not item:
            return

        name = item.get("name", "")
        parent_id = item.get("parentReference", {}).get("id")

        if parent_id:
            children = self._parent_to_children.get(parent_id, [])
            if item_id in children:
                children.remove(item_id)
            self._parent_name_to_id.pop((parent_id, name), None)

    def _discover_root(self):
        """
        After all items are loaded, identify the OneDrive root item ID.
        The root is the parent_id referenced by items but absent from the DB
        (the root item itself is skipped during sync in Operations.py).
        """
        if self._root_id:
            return  # already known from the inode file

        all_parent_ids = {
            item.get("parentReference", {}).get("id")
            for item in self._id_to_item.values()
            if item.get("parentReference", {}).get("id")
        }
        root_candidates = all_parent_ids - set(self._id_to_item.keys())

        if not root_candidates:
            logger.warning("Could not discover root item ID — DB may be empty")
            return

        if len(root_candidates) > 1:
            logger.warning(f"Multiple root candidates found: {root_candidates}")

        self._root_id = root_candidates.pop()
        # Ensure root is always inode 1
        self._id_to_inode[self._root_id] = ROOT_INODE
        self._inode_to_id[ROOT_INODE] = self._root_id
        self._save_inode_mapping()
        logger.debug(f"Discovered root item ID: {self._root_id}")

    def _maybe_update_root(self, item_dict):
        """After an upsert, check if root discovery is now possible."""
        if not self._root_id:
            self._discover_root()

    # ------------------------------------------------------------------
    # Private: inode management
    # ------------------------------------------------------------------

    def _assign_inode(self, item_id):
        inode = self._next_inode
        self._next_inode += 1
        self._id_to_inode[item_id] = inode
        self._inode_to_id[inode] = item_id

    def _load_inode_mapping(self):
        if not os.path.exists(INODE_FILE):
            return
        try:
            with open(INODE_FILE, "r") as f:
                data = json.load(f)
            # JSON keys are always strings — convert inode values back to int
            self._id_to_inode = {k: int(v) for k, v in data.get("id_to_inode", {}).items()}
            self._inode_to_id = {int(v): k for k, v in self._id_to_inode.items()}
            self._next_inode = data.get("next_inode", 2)
            self._root_id = data.get("root_id")
            logger.debug(f"Loaded inode mapping: {len(self._id_to_inode)} entries")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not load inode mapping ({e}) — starting fresh")

    def _save_inode_mapping(self):
        """Atomically write inode mapping to disk."""
        data = {
            "id_to_inode": self._id_to_inode,
            "next_inode": self._next_inode,
            "root_id": self._root_id,
        }
        tmp = INODE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, INODE_FILE)

    # ------------------------------------------------------------------
    # Private: DB file I/O
    # ------------------------------------------------------------------

    def _save_item_to_db(self, item_id, item_dict):
        os.makedirs(ONEDRIVE_DB_FOLDER, exist_ok=True)
        path = os.path.join(ONEDRIVE_DB_FOLDER, item_id)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(item_dict, f)
        os.replace(tmp, path)

    def _delete_item_from_db(self, item_id):
        path = os.path.join(ONEDRIVE_DB_FOLDER, item_id)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
