import argparse
import shutil
import yaml
import os


BLACKLISTED_LIST = [
    # caused multiple FPs, and as RockMongo is not maintained anymore, let's skip it
    "http/vulnerabilities/other/rockmongo-xss.yaml",
    # Too small impact to report
    "http/exposed-panels/webeditors-check-detect.yaml",
    # Mostly meant to be publicly accessible
    "http/exposed-panels/bigbluebutton-login.yaml",
    "http/exposed-panels/ilias-panel.yaml",
    "http/exposed-panels/office-webapps-panel.yaml",
    "http/exposed-panels/onlyoffice-login-panel.yaml",
    "http/exposed-panels/opensis-panel.yaml",
    "http/exposed-panels/projectsend-login.yaml",
    "http/exposed-panels/rocketchat-panel.yaml",
    # Mostly Moodle config
    "http/exposures/configs/behat-config.yaml",
]

BLACKLISTED_TAGS = [
    "ssl",
    "tls",
]

BLACKLISTED_IDS = [
    "wildcard-tls",
    "weak-cipher-suites",
    "untrusted-root-certificate",
    "mismatched-ssl-certificate",
    "insecure-cipher-suite-detect",
    "expired-ssl",
    "ssl-issuer",
    "self-signed-ssl",
    "revoked-ssl-certificate",
    "deprecated-tls",
    "wordpress-wp-mail-logging",
    "wordpress-extendify",
    "wordpress-unlimited-elements-for-elementor",
    "wordpress-burst-statistics",
    "http-missing-security-headers",
    "tailon-panel",
    "CVE-2024-51483", # check cvss 
    "credit-card-number-detect", # weak matchers, FPs; excluded by name on every sync
]

BLACKLISTED_DIRS = [
    "workflows",
    "code",
    "dast",
    "file",
    "headless",
    "local/workflows",
    "local/cloud",
    "local/dast",
    "local/file",
    "local/headless",
]

BLACKLISTED_SEVERITIES = [
    "info",
]

############################################################################################
# WHITELISTED TEMPLATES
# These templates are not blacklisted, but are whitelisted to be included in the final list
############################################################################################

WHITELISTED_TAGS = [
    "springboot",
    "misconfig",
    "atlassian",
    "jira",
    "default-login",
    "kubernetes",
    "solr",
    "firebase",
]

WHITELISTED_DIRS = [
    "http/exposures",
    "http/vulnerabilities",
    "http/exposed-panels/fortinet",
    "http/exposed-panels/mikrotik",
    "http/exposed-panels/jboss",
    "http/exposed-panels/cisco",
    "http/exposed-panels/zyxel",
    "http/technologies/dell",
    "http/technologies/kubernetes",
    "http/technologies/graylog",
    "http/technologies/telerik",
    "http/technologies/vmware",
    "http/token-spray",
    "network/enumeration",
    "network/detection",
]

WHITELISTED_IDS = [
    "sql-server-reporting",
    "security-txt",
    # requires ruby, so not supported for now
    # "CVE-2016-9299",
    # "CVE-2024-45488",
    # "CVE-2024-8698",
]

CUSTOM_TEMPLATES_DIRS = [
    "local",
]


def sanitize_value(value):
    # return value.decode("utf-8", errors="replace").replace("\x00", "\uFFFD")
    return value.replace("\x00", "\uFFFD")


def sanitize_values_in_list(values):
    return [sanitize_value(value) for value in values]


def parse_nuclei_templates(templates_path):
    # walk the directory, find all the yaml files (ignore workflow files)
    templates = []
    for root, dirs, files in os.walk(templates_path):
        for file in files:
            if file.endswith(".yaml"):
                templates.append(os.path.join(root, file))

    # iterate over the files, parse the yaml files
    templates_data = []
    for template in templates:
        with open(template, "r") as f:
            data = yaml.safe_load(f)
            path = sanitize_value("/".join(template.split("/")[1:]))
            templates_data.append(
                {
                    "id": sanitize_value(data["id"]),
                    "name": sanitize_value(data["info"]["name"]),
                    "description": (
                        sanitize_value(data["info"]["description"])
                        if "description" in data["info"]
                        else ""
                    ),
                    "author": (
                        sanitize_value(data["info"]["author"]).split(",")
                        if type(data["info"]["author"]) is not list
                        or "," not in data["info"]["author"]
                        else data["info"]["author"]
                    ),
                    "severity": (
                        sanitize_value(data["info"]["severity"])
                        if "severity" in data["info"]
                        else ""
                    ),
                    "tags": (
                        (
                            sanitize_value(data["info"]["tags"]).split(",")
                            if type(data["info"]["tags"]) is not list
                            or "," not in data["info"]["tags"]
                            else data["info"]["tags"]
                        )
                        if "tags" in data["info"]
                        else []
                    ),
                    "reference": (
                        sanitize_values_in_list(data["info"]["reference"])
                        if "reference" in data["info"]
                        else []
                    ),
                    "path": path,
                    "is_custom": any(
                        path.startswith(f"{custom_dir}/")
                        for custom_dir in CUSTOM_TEMPLATES_DIRS
                    ),
                    "type": "code" if "code" in data else None,
                }
            )

    return templates_data


def main():
    parser = argparse.ArgumentParser(description="Remove blacklisted Nuclei templates")
    parser.add_argument(
        "--templates-path",
        "-t",
        type=str,
        help="Path to the Nuclei templates directory",
    )
    # dry run argument
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run the script, don't delete any files",
        default=False,
    )
    args = parser.parse_args()

    # no args provided, print help
    if not args.templates_path:
        parser.print_help()
        return

    templates = parse_nuclei_templates(args.templates_path)

    # Build a mapping from ID to templates
    id_to_templates = {}
    for template in templates:
        template_id = template["id"]
        if template_id not in id_to_templates:
            id_to_templates[template_id] = []
        id_to_templates[template_id].append(template)

    # Identify IDs with duplicates between custom and non-custom directories
    duplicate_ids = []
    for template_id, tmpl_list in id_to_templates.items():
        has_custom = any(t["is_custom"] for t in tmpl_list)
        has_non_custom = any(not t["is_custom"] for t in tmpl_list)
        if has_custom and has_non_custom:
            duplicate_ids.append(template_id)

    # Delete blacklisted directories
    for blacklisted_dir in BLACKLISTED_DIRS:
        if os.path.exists(os.path.join(args.templates_path, blacklisted_dir)):
            print(f"Deleting {blacklisted_dir}/")
            if not args.dry_run:
                shutil.rmtree(os.path.join(args.templates_path, blacklisted_dir))

    # Iterate over the templates, remove the blacklisted ones
    for template in templates:
        # New Feature: Remove custom templates if duplicate exists
        if template["is_custom"] and template["id"] in duplicate_ids:
            print(f"Deleting custom template {template['path']} due to duplicate ID")
            if not args.dry_run:
                try:
                    os.remove(os.path.join(args.templates_path, template["path"]))
                except FileNotFoundError:
                    print(f"File {template['path']} not found")
            continue  # Skip further checks since it's already handled

        # Check if the template is whitelisted
        if any(
            tag in template["tags"]
            for tag in WHITELISTED_TAGS
            if template["id"] not in BLACKLISTED_IDS
        ):
            continue

        if any(
            t in template["path"]
            for t in WHITELISTED_DIRS
            if template["id"] not in BLACKLISTED_IDS
        ):
            continue

        if template["id"] in WHITELISTED_IDS and template["id"] not in BLACKLISTED_IDS:
            continue
        
        # alreadt checked whitelisted ids
        # so if not whitelisted and type == code, then delete also

        if (
            template["path"] in BLACKLISTED_LIST
            or template["id"] in BLACKLISTED_IDS
            or template["severity"] in BLACKLISTED_SEVERITIES
            # or template["tags"] only consists of tags from BLACKLISTED_TAGS
            or all(tag in BLACKLISTED_TAGS for tag in template["tags"])
            or template["type"] == "code"
        ):
            print(f"Deleting {template['path']}")
            if not args.dry_run:
                try:
                    os.remove(os.path.join(args.templates_path, template["path"]))
                except FileNotFoundError:
                    print(f"File {template['path']} not found")


if __name__ == "__main__":
    main()
