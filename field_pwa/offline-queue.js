const JOBHUB_DB = "pb-jobhub-field";
const QUEUE_STORE = "queued-events";

function openJobHubQueue() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(JOBHUB_DB, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(QUEUE_STORE)) {
        db.createObjectStore(QUEUE_STORE, { keyPath: "idempotency_key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function queueJobHubEvent(event) {
  if (!event || !event.idempotency_key) {
    throw new Error("A JobHub offline event requires an idempotency key.");
  }
  const db = await openJobHubQueue();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(QUEUE_STORE, "readwrite");
    tx.objectStore(QUEUE_STORE).put({
      ...event,
      queued_at: event.queued_at || new Date().toISOString(),
      sync_status: "pending",
    });
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}

export async function listQueuedJobHubEvents() {
  const db = await openJobHubQueue();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(QUEUE_STORE, "readonly");
    const request = tx.objectStore(QUEUE_STORE).getAll();
    request.onsuccess = () => resolve(request.result || []);
    request.onerror = () => reject(request.error);
  });
}

export async function removeQueuedJobHubEvent(idempotencyKey) {
  const db = await openJobHubQueue();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(QUEUE_STORE, "readwrite");
    tx.objectStore(QUEUE_STORE).delete(idempotencyKey);
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}

export async function flushJobHubQueue(syncEndpoint, fetchImpl = fetch) {
  const queued = await listQueuedJobHubEvents();
  const summary = { sent: 0, retained: 0 };
  for (const event of queued) {
    try {
      const response = await fetchImpl(syncEndpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": event.idempotency_key,
        },
        body: JSON.stringify(event.payload),
      });
      if (!response.ok) throw new Error(`Sync failed with ${response.status}`);
      await removeQueuedJobHubEvent(event.idempotency_key);
      summary.sent += 1;
    } catch (_error) {
      summary.retained += 1;
    }
  }
  return summary;
}
