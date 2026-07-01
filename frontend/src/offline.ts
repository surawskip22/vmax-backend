type QueuedFile = {
  name: string;
  type: string;
  blob: Blob;
  clientRef: string;
};

export type OfflineScopeKey = string;

export type QueuedEntry = {
  id: string;
  scopeKey: OfflineScopeKey;
  projectId: string;
  guestToken?: string;
  payload: {
    kind: "update" | "problem";
    body: string;
    transcript?: string;
    stage_id?: string;
    client_ref: string;
  };
  files: QueuedFile[];
  createdAt: number;
  error?: string;
};

const DB_NAME = "pan-majster-offline";
const STORE = "entries";

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore(STORE, { keyPath: "id" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function queueEntry(entry: QueuedEntry): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).put(entry);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

export async function queuedEntries(scopeKey: OfflineScopeKey): Promise<QueuedEntry[]> {
  const db = await openDb();
  const result = await new Promise<Array<QueuedEntry | (Partial<QueuedEntry> & { id: string })>>((resolve, reject) => {
    const request = db.transaction(STORE).objectStore(STORE).getAll();
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  db.close();
  return result
    .filter((entry): entry is QueuedEntry => entry.scopeKey === scopeKey)
    .sort((a, b) => a.createdAt - b.createdAt);
}

export async function queuedEntryCount(scopeKey: OfflineScopeKey): Promise<number> {
  return (await queuedEntries(scopeKey)).length;
}

export async function deleteQueuedEntry(id: string): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).delete(id);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}
