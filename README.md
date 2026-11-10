# Parfumerie Product Downloader

Python Flask crawler for public product catalog data on https://parfumerie.vn. It respects robots.txt; if robots.txt is unavailable it stops. This is a best-effort extractor; website changes may require adjustments. No claim is made that all products or variants can be recovered.

## Render deployment

1. Upload all project files to a private GitHub repository.
2. Render > New > Blueprint > select repository; deploy using render.yaml.
3. Render automatically generates ADMIN_TOKEN; retrieve it under Environment. Keep it secret.
4. Open the Render URL, enter ADMIN_TOKEN, select maximum pages, and start.
5. Download ZIP containing products.csv, images.csv and downloaded images.

The included Blueprint uses a paid Starter service with a 1GB persistent disk at /var/data. This avoids losing output across restarts, but a currently running crawl does not automatically resume after a restart. Starting again reloads saved products but revisits navigation pages. Increase disk size if needed. The app uses one Gunicorn worker and an in-process background thread; not intended for high-concurrency use.

## Local run

```bash
pip install -r requirements.txt
export ADMIN_TOKEN=change-this-secret
python app.py
```

Windows PowerShell: `$env:ADMIN_TOKEN='change-this-secret'; python app.py`.

## Limits

- The crawler uses public HTML, internal links and JSON-LD Product metadata. It may miss JS-only pages, paginated catalog URLs with query strings, product variants, images outside known selectors, or non-JSON-LD fields.
- Only downloads images from expected site/CDN hosts, with a 12 MB per-image limit.
- It never accesses private accounts or bypasses robots.txt or other access restrictions.
- Source descriptions and images may be copyrighted. Obtain authorization before republishing.
- No complete crawl of the live site has been performed in the build environment.
