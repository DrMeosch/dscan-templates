import requests
import os
from pathlib import Path
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed

# Set your GitHub Personal Access Token as an environment variable
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", None) or os.environ.get(
    "DSCAN_GITHUB_TOKEN", None
)

headers = {}
if GITHUB_TOKEN:
    headers["Authorization"] = f"token {GITHUB_TOKEN}"

OWNER = "projectdiscovery"
REPO = "nuclei-templates"

API_BASE_URL = "https://api.github.com"


def get_all_search_results(url, headers, params):
    results = []
    print(f"Fetching {url} with params {params}")
    while url:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code != 200:
            print(f"Error fetching {url}: {response.status_code} {response.text}")
            break
        data = response.json()
        results.extend(data.get("items", []))
        if "next" in response.links:
            url = response.links["next"]["url"]
            params = None  # Parameters are already included in the next URL
        else:
            url = None
    return results


def get_all_pages(url, headers):
    results = []
    while url:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"Error fetching {url}: {response.status_code} {response.text}")
            break
        results.extend(response.json())
        if "next" in response.links:
            url = response.links["next"]["url"]
        else:
            url = None
    return results


def main():
    search_query = f"repo:{OWNER}/{REPO} is:pull-request is:open label:Done"
    search_url = f"{API_BASE_URL}/search/issues"
    params = {"q": search_query, "per_page": 100}
    pr_items = get_all_search_results(search_url, headers, params)
    print(f"Found {len(pr_items)} PRs with label 'Done'")
    local_folder = "local"
    os.makedirs(local_folder, exist_ok=True)

    download_tasks = []

    for pr_item in pr_items:
        pr_number = pr_item["number"]
        pr_url = f"{API_BASE_URL}/repos/{OWNER}/{REPO}/pulls/{pr_number}"
        pr_response = requests.get(pr_url, headers=headers)
        if pr_response.status_code != 200:
            print(
                f"Error fetching PR #{pr_number}: {pr_response.status_code} {pr_response.text}"
            )
            continue
        pr_info = pr_response.json()
        head_sha = pr_info["head"]["sha"]
        print(f"Processing PR #{pr_number} - {pr_info['title']}")

        # Get the list of files in the PR
        files_url = pr_info["_links"]["self"]["href"] + "/files"
        files = get_all_pages(files_url, headers)

        for file in files:
            filename = file["filename"]
            # filename should end up with .yaml
            if not filename.endswith(".yaml"):
                continue
            status = file["status"]
            if status in ["added", "modified"]:
                # Prepare the download task
                encoded_filename = quote(filename)
                raw_url = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/{head_sha}/{encoded_filename}"
                local_file_path = os.path.join(local_folder, filename)
                download_tasks.append({
                    'raw_url': raw_url,
                    'local_file_path': local_file_path,
                    'filename': filename,
                })

    print(f"Total files to download: {len(download_tasks)}")

    # Function to download a single file
    def download_file(task):
        raw_url = task['raw_url']
        local_file_path = task['local_file_path']
        filename = task['filename']
        try:
            file_response = requests.get(raw_url, headers=headers)
            if file_response.status_code != 200:
                print(
                    f"Error fetching file {filename}: {file_response.status_code} {file_response.text}"
                )
                return
            file_content = file_response.content
            # Ensure directory exists
            os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
            with open(local_file_path, "wb") as f:
                f.write(file_content)
            print(f"Saved file {local_file_path}")
        except Exception as e:
            print(f"Error downloading file {filename}: {e}")

    # Use ThreadPoolExecutor to download files concurrently
    max_workers = 20

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(download_file, task) for task in download_tasks]
        # Wait for all tasks to complete
        for future in as_completed(futures):
            pass  # You can handle exceptions here if needed


if __name__ == "__main__":
    main()
