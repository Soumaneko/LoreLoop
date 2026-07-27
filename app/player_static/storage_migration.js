export function migrateStorageKey(storage, legacyKey, currentKey) {
  try {
    if (storage.getItem(currentKey) === null) {
      const legacy = storage.getItem(legacyKey);
      if (legacy !== null) storage.setItem(currentKey, legacy);
    }
    storage.removeItem(legacyKey);
    return true;
  } catch (_error) {
    return false;
  }
}
