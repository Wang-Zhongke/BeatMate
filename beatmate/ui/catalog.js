// A slower response from a previous search must never replace the current page.
export function createCatalogLoader(fetchPage, applyPage) {
  let sequence = 0,
    pending = null;
  return async function load(query) {
    const key = JSON.stringify(query);
    if (pending?.key === key) return pending.promise;
    const token = ++sequence;
    const promise = (async () => {
      try {
        const page = await Promise.resolve().then(() => fetchPage(query));
        if (token === sequence) await applyPage(page);
      } catch (error) {
        if (token === sequence) throw error;
      } finally {
        if (pending?.token === token) pending = null;
      }
    })();
    pending = { key, token, promise };
    return promise;
  };
}
