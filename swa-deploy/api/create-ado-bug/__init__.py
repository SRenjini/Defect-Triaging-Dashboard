"""
Azure Function: ADO Bug Creation Proxy
Securely proxies ADO work item creation requests.
The PAT is stored in Application Settings (environment variable), never exposed to the client.
"""
import azure.functions as func
import json
import os
import logging
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from base64 import b64encode


def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("ADO bug creation proxy invoked")

    # ── Read secrets from environment ──
    pat = os.environ.get("ADO_PAT")
    if not pat:
        logging.error("ADO_PAT not configured in Application Settings")
        return func.HttpResponse(
            json.dumps({"error": "Server configuration error: ADO_PAT not set"}),
            status_code=500,
            mimetype="application/json"
        )

    org = os.environ.get("ADO_ORGANIZATION", "")
    project = os.environ.get("ADO_PROJECT", "")

    # ── Parse request body ──
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON in request body"}),
            status_code=400,
            mimetype="application/json"
        )

    # ── Build ADO work item payload ──
    work_item_data = [
        {"op": "add", "path": "/fields/System.Title", "value": body.get("title", "")},
        {"op": "add", "path": "/fields/System.Description", "value": body.get("description", "")},
        {"op": "add", "path": "/fields/Microsoft.VSTS.TCM.ReproSteps", "value": body.get("reproSteps", "")},
        {"op": "add", "path": "/fields/Microsoft.VSTS.Common.Severity", "value": body.get("severity", "3 - Medium")},
        {"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority", "value": int(body.get("priority", 0))},
        {"op": "add", "path": "/fields/EDMAgile.Application", "value": body.get("application", "TRC")},
        {"op": "add", "path": "/fields/EDMAgile.Impact", "value": body.get("impact", "03 - Medium")},
        {"op": "add", "path": "/fields/Custom.DEF_DefectInProd", "value": body.get("defectInProd", "No")},
        {"op": "add", "path": "/fields/Custom.DEF_EnvironmentFoundIn", "value": body.get("environmentFoundIn", "QA")},
        {"op": "add", "path": "/fields/Custom.Def_HowFound", "value": body.get("howFound", "Manual")},
        {"op": "add", "path": "/fields/Custom.TaxForm", "value": body.get("taxForm", "")},
        {"op": "add", "path": "/fields/Custom.3f895184-c6ac-4e9a-b8b7-3730153b420c", "value": body.get("taxEntity", "")},
        {"op": "add", "path": "/fields/Custom.HRBReason", "value": "New"},
        {"op": "add", "path": "/fields/Custom.HRBCalculatedPriority", "value": int(body.get("priority", 0))},
        {"op": "add", "path": "/fields/Custom.BugOverriddenPriority", "value": 0},
        {"op": "add", "path": "/fields/Custom.BugOverriddenPriorityReason", "value": "NA"},
        {"op": "add", "path": "/fields/Custom.HRBGHCPUsedforCodeGeneration", "value": "No"},
        {"op": "add", "path": "/fields/Custom.HRBGHCPUsedforCodeValidation", "value": "No"},
        {"op": "add", "path": "/fields/Custom.HRBGHCPUsedforTestCases", "value": "No"},
        {"op": "add", "path": "/fields/Custom.HRBGHCPUsedforDocumentation", "value": "No"},
        {"op": "add", "path": "/fields/Custom.HRBGHCPUsedforOtherPleaseExplain", "value": "No"},
        {"op": "add", "path": "/fields/System.Tags", "value": body.get("tags", "")},
        {"op": "add", "path": "/fields/System.AreaPath", "value": "TRC Automation"},
        {"op": "add", "path": "/fields/System.IterationPath", "value": "TRC Automation"}
    ]

    # ── Call ADO API ──
    ado_url = f"https://dev.azure.com/{org}/{project}/_apis/wit/workitems/$Bug?api-version=7.1"
    auth_token = b64encode(f":{pat}".encode()).decode()

    request = Request(
        ado_url,
        data=json.dumps(work_item_data).encode("utf-8"),
        headers={
            "Content-Type": "application/json-patch+json",
            "Authorization": f"Basic {auth_token}"
        },
        method="POST"
    )

    try:
        with urlopen(request) as response:
            result = json.loads(response.read().decode("utf-8"))
            logging.info(f"ADO Bug created: ID={result.get('id')}")
            return func.HttpResponse(
                json.dumps(result),
                status_code=200,
                mimetype="application/json"
            )
    except HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else str(e)
        logging.error(f"ADO API error: {e.code} - {error_body}")
        return func.HttpResponse(
            json.dumps({"error": f"ADO API error: {e.code}", "details": error_body}),
            status_code=e.code,
            mimetype="application/json"
        )
    except URLError as e:
        logging.error(f"Network error: {e.reason}")
        return func.HttpResponse(
            json.dumps({"error": f"Network error: {str(e.reason)}"}),
            status_code=502,
            mimetype="application/json"
        )
