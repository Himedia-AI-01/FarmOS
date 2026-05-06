const FALLBACK_KEYWORDS = 'farm,produce';
const FALLBACK_LOCK_BASE = 800;

export function productFallbackImage(productId: number | string | undefined, size: number) {
  const id = Number(productId);
  const lock = FALLBACK_LOCK_BASE + (Number.isFinite(id) ? id : 0);
  return `https://loremflickr.com/${size}/${size}/${FALLBACK_KEYWORDS}?lock=${lock}`;
}

export function defaultProductImage(size: number) {
  return `https://loremflickr.com/${size}/${size}/${FALLBACK_KEYWORDS}?lock=${FALLBACK_LOCK_BASE}`;
}
