"""
Azure Function: Refresh QA Triage Data from Cosmos DB
Queries Cosmos DB and returns the full qa_triage_data.json payload.
Cosmos credentials are stored in Application Settings (environment variables).
"""
import azure.functions as func
import json
import os
import logging
from datetime import datetime
from collections import defaultdict

# ── Tax Expert Comment Classification ──
TAX_EXPERT_TAGS = {
    'Form Identification Error': {
        'keywords': ['form name', 'form details', 'instructions', 'schedule', 'wrong form'],
        'severity': 'high'
    },
    'Line Reference Inaccuracy': {
        'keywords': ['line', 'reference', 'line number', 'incorrect line', 'wrong line'],
        'severity': 'high'
    },
    'Calculation Change Missing': {
        'keywords': ['calc', 'calculation', 'sum', 'total', 'computation', 'formula'],
        'severity': 'high'
    },
    'Change Type Misclassification': {
        'keywords': ['renumber', 'really a', 'actually', 'misclassif', 'wrong bucket', 'not a'],
        'severity': 'high'
    },
    'Context Irrelevance': {
        'keywords': ['irrelevant', 'not relevant', 'wrong context', 'unrelated'],
        'severity': 'medium'
    },
    'Content Verbosity': {
        'keywords': ['wordy', 'too long', 'verbose', 'make it short', 'too much'],
        'severity': 'low'
    },
    'Clarity & Precision': {
        'keywords': ['lacks clarity', 'unclear', 'confusing', 'vague', 'imprecise'],
        'severity': 'medium'
    },
    'Section Specificity': {
        'keywords': ['section', 'area', 'part', 'clearly defined', 'location', 'where'],
        'severity': 'medium'
    },
    'Change Completeness': {
        'keywords': ['missing', 'incomplete', 'both', 'also', 'additional', 'all'],
        'severity': 'high'
    },
    'Unnecessary Content': {
        'keywords': ['unnecessary', 'does not add value', 'remove', 'not needed'],
        'severity': 'low'
    }
}

FEEDBACK_FIELDS = ['context', 'system_inference', 'tax_rule_change', 'category']


def classify_tax_comment(comment_text):
    """Classify a comment using US tax expert knowledge"""
    if not comment_text or not comment_text.strip():
        return []
    comment_lower = comment_text.lower()
    matched_tags = []
    for tag_name, tag_info in TAX_EXPERT_TAGS.items():
        for keyword in tag_info['keywords']:
            if keyword.lower() in comment_lower:
                matched_tags.append({'name': tag_name, 'severity': tag_info['severity']})
                break
    return matched_tags if matched_tags else [{'name': 'General Issue', 'severity': 'medium'}]


def extract_doc_number(doc_id):
    parts = doc_id.split('_')
    for part in parts:
        if part.isdigit() and len(part) >= 4:
            return part
    return str(hash(doc_id) % 99999).zfill(5)


def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Refresh data function invoked")

    # ── Read Cosmos credentials from environment ──
    cosmos_endpoint = os.environ.get("COSMOS_ENDPOINT")
    cosmos_key = os.environ.get("COSMOS_KEY")
    cosmos_db = os.environ.get("COSMOS_DATABASE", "tcaDB")
    cosmos_container = os.environ.get("COSMOS_CONTAINER", "reg_documents")

    if not cosmos_endpoint or not cosmos_key:
        return func.HttpResponse(
            json.dumps({"error": "COSMOS_ENDPOINT and COSMOS_KEY must be set in Application Settings"}),
            status_code=500,
            mimetype="application/json"
        )

    # ── Connect to Cosmos DB ──
    try:
        from azure.cosmos import CosmosClient
        client = CosmosClient(cosmos_endpoint, cosmos_key)
        database = client.get_database_client(cosmos_db)
        container = database.get_container_client(cosmos_container)
    except Exception as e:
        logging.error(f"Cosmos connection error: {e}")
        return func.HttpResponse(
            json.dumps({"error": f"Cosmos connection failed: {str(e)}"}),
            status_code=500,
            mimetype="application/json"
        )

    # ── Query data ──
    try:
        logging.info("Fetching Documents...")
        documents = list(container.query_items(
            "SELECT c.id, c.document_id, c.document_title, c.document_type, "
            "c.tax_entity, c.tax_year, c.revision, c.created_at, c.created_by, "
            "c.forms, c.source "
            "FROM c WHERE c.entity_type = 'Document' AND (c.is_deleted = false OR NOT IS_DEFINED(c.is_deleted))",
            enable_cross_partition_query=True
        ))

        logging.info("Fetching TRCs...")
        trcs = list(container.query_items(
            "SELECT c.id, c.job_id, c.document_id, c.title, c.classification, "
            "c.change_type, c.bucket, c.trc_description, c.your_reasoning, "
            "c.description_of_change, c.line_number_and_reference, "
            "c.review_status, c.approval_status, c.confident, "
            "c.override, c.feedback, "
            "c.created_at, c.created_by, c.reviewed_by, c.reviewed_at, "
            "c.page, c.additional_details_needed "
            "FROM c WHERE c.entity_type = 'TaxRuleChange'",
            enable_cross_partition_query=True
        ))

        logging.info("Fetching Feedbacks...")
        feedbacks = list(container.query_items(
            "SELECT c.id, c.trc_id, c.document_id, "
            "c.context, c.system_inference, c.tax_rule_change, c.category, "
            "c.created_at, c.created_by "
            "FROM c WHERE c.entity_type = 'TaxRuleChangeCustomFeedbacks'",
            enable_cross_partition_query=True
        ))

        logging.info("Fetching ComparisonJobs...")
        comp_jobs = list(container.query_items(
            "SELECT c.id, c.job_id, c.document_id, c.compared_doc_id, "
            "c.created_by, c.created_at, c.tcat_work_item "
            "FROM c WHERE c.entity_type = 'ComparisonJob'",
            enable_cross_partition_query=True
        ))

        logging.info("Fetching TaxRuleChangeJob links...")
        trc_jobs = list(container.query_items(
            "SELECT c.job_id, c.comparison_job_id, c.document_id "
            "FROM c WHERE c.entity_type = 'TaxRuleChangeJob'",
            enable_cross_partition_query=True
        ))

    except Exception as e:
        logging.error(f"Query error: {e}")
        return func.HttpResponse(
            json.dumps({"error": f"Cosmos query failed: {str(e)}"}),
            status_code=500,
            mimetype="application/json"
        )

    # ── Build enriched data (same logic as export_qa_triage.py) ──
    doc_by_id = {d['document_id']: d for d in documents}
    feedback_by_trc = {f['trc_id']: f for f in feedbacks}
    trc_job_to_comp = {tj['job_id']: tj['comparison_job_id'] for tj in trc_jobs}
    comp_job_map = {j['job_id']: j for j in comp_jobs}

    entities = sorted(set(d.get('tax_entity', 'Unknown') for d in documents if d.get('tax_entity')))
    tax_years = sorted(set(d.get('tax_year', 0) for d in documents if d.get('tax_year')), reverse=True)

    trcs_by_doc = defaultdict(list)
    for trc in trcs:
        doc_id = trc.get('document_id', '')
        if doc_id:
            trcs_by_doc[doc_id].append(trc)

    enriched_documents = []
    for doc in documents:
        doc_id = doc['document_id']
        doc_trcs = trcs_by_doc.get(doc_id, [])
        if not doc_trcs:
            continue

        doc_number = extract_doc_number(doc_id)
        enriched_trcs = []

        for trc in doc_trcs:
            fb = feedback_by_trc.get(trc['id'], {})
            enriched_fields = {}
            all_tags = []

            for f in FEEDBACK_FIELDS:
                field_data = fb.get(f, {})
                if isinstance(field_data, dict):
                    comments = field_data.get('comments', '').strip() if field_data.get('comments') else ''
                    tags = classify_tax_comment(comments) if comments else []
                    all_tags.extend([tag['name'] for tag in tags])
                    enriched_fields[f] = {
                        'status': field_data.get('status', ''),
                        'comments': comments,
                        'tags': tags
                    }
                else:
                    enriched_fields[f] = {'status': '', 'comments': '', 'tags': []}

            review_status = trc.get('review_status', 'NotStarted')
            has_feedback = trc['id'] in feedback_by_trc
            if not review_status or review_status == '':
                review_status = 'FirstReviewCompleted' if has_feedback else 'NotStarted'

            job_id = trc.get('job_id', '')
            comp_job_id = trc_job_to_comp.get(job_id, '')
            comp_job_info = comp_job_map.get(comp_job_id, {})
            ado_work_item = comp_job_info.get('tcat_work_item', '')

            ado_number = ''
            if ado_work_item:
                if isinstance(ado_work_item, dict) and 'Id' in ado_work_item:
                    ado_number = str(ado_work_item['Id'])
                elif isinstance(ado_work_item, (int, str)):
                    ado_number = str(ado_work_item)

            trc_number = str(hash(trc['id']) % 9999).zfill(4)

            enriched_trcs.append({
                'id': trc['id'],
                'trc_number': trc_number,
                'job_id': job_id,
                'ado_work_item': ado_work_item,
                'ado_number': ado_number,
                'title': trc.get('title', ''),
                'bucket': trc.get('bucket', ''),
                'classification': trc.get('classification', ''),
                'change_type': trc.get('change_type', ''),
                'review_status': review_status,
                'approval_status': trc.get('approval_status', ''),
                'confident': trc.get('confident', ''),
                'trc_description': trc.get('trc_description', ''),
                'your_reasoning': trc.get('your_reasoning', ''),
                'description_of_change': trc.get('description_of_change', ''),
                'line_reference': trc.get('line_number_and_reference', ''),
                'page': trc.get('page', ''),
                'created_at': trc.get('created_at', ''),
                'created_by': trc.get('created_by', ''),
                'reviewed_by': trc.get('reviewed_by') or fb.get('created_by', '') or '',
                'reviewed_at': trc.get('reviewed_at', ''),
                'has_feedback': has_feedback,
                'fields': enriched_fields,
                'all_tags': list(set(all_tags))
            })

        # Only count actual TRCs (POTENTIAL_TRC, TRC_REQUIRED) — NOT_TRC excluded
        actual_trcs = [t for t in enriched_trcs if t['classification'] not in ('NOT_TRC', '')]
        total_trcs = len(actual_trcs)
        first_review = [t for t in actual_trcs if t['review_status'] == 'FirstReviewCompleted']
        trcs_with_fb = [t for t in actual_trcs if t['has_feedback']]

        trc_desc_approved = 0
        trc_desc_with_comments = 0
        for t in first_review:
            trc_field = t['fields'].get('tax_rule_change', {})
            if trc_field.get('comments'):
                trc_desc_with_comments += 1
            if trc_field.get('status') == 'Approved':
                trc_desc_approved += 1

        field_stats = {}
        for f in FEEDBACK_FIELDS:
            approved = sum(1 for t in first_review if t['fields'].get(f, {}).get('status') == 'Approved')
            not_approved = sum(1 for t in first_review if t['fields'].get(f, {}).get('status') == 'NotApproved')
            with_comments = sum(1 for t in first_review if t['fields'].get(f, {}).get('comments'))
            field_stats[f] = {
                'approved': approved,
                'not_approved': not_approved,
                'with_comments': with_comments,
                'total_reviewed': approved + not_approved
            }

        enriched_documents.append({
            'document_id': doc_id,
            'document_number': doc_number,
            'document_title': doc.get('document_title', ''),
            'document_type': doc.get('document_type', ''),
            'tax_entity': doc.get('tax_entity', ''),
            'tax_year': doc.get('tax_year', ''),
            'revision': doc.get('revision', ''),
            'created_at': doc.get('created_at', ''),
            'created_by': doc.get('created_by', ''),
            'forms': doc.get('forms', []),
            'total_trcs': total_trcs,
            'first_review_completed': len(first_review),
            'trcs_with_feedback': len(trcs_with_fb),
            'trc_desc_approved': trc_desc_approved,
            'trc_desc_with_comments': trc_desc_with_comments,
            'field_stats': field_stats,
            'total_changes': len(enriched_trcs),  # all detected changes incl. NOT_TRC
            'trcs': enriched_trcs,
        })

    # ── Global summary ──
    # Only count actual TRCs (exclude NOT_TRC) for global summary
    all_actual_trcs = [t for d in enriched_documents for t in d['trcs'] if t['classification'] not in ('NOT_TRC', '')]
    all_first_review = [t for t in all_actual_trcs if t['review_status'] == 'FirstReviewCompleted']
    total_first_review = len(all_first_review)

    tag_counts = {}
    for t in all_first_review:
        for tag in t['all_tags']:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    top_issue_threshold = total_first_review * 0.7
    top_issue_tags = {tag: count for tag, count in tag_counts.items() if count > top_issue_threshold} if total_first_review > 0 else {}

    trc_desc_no_comment = sum(1 for t in all_first_review if not t['fields'].get('tax_rule_change', {}).get('comments'))
    trc_desc_acceptance = round(trc_desc_no_comment / total_first_review * 100, 1) if total_first_review else 0

    total_fields = total_first_review * 4
    approved_fields = sum(
        1 for t in all_first_review
        for f in FEEDBACK_FIELDS
        if t['fields'].get(f, {}).get('status') == 'Approved'
    )
    all_field_approval = round(approved_fields / total_fields * 100, 1) if total_fields else 0

    global_field_stats = {}
    for f in FEEDBACK_FIELDS:
        approved = sum(1 for t in all_first_review if t['fields'].get(f, {}).get('status') == 'Approved')
        not_approved = sum(1 for t in all_first_review if t['fields'].get(f, {}).get('status') == 'NotApproved')
        with_comments = sum(1 for t in all_first_review if t['fields'].get(f, {}).get('comments'))
        total_reviewed = approved + not_approved
        global_field_stats[f] = {
            'approved': approved,
            'not_approved': not_approved,
            'with_comments': with_comments,
            'total_reviewed': total_reviewed,
            'approval_rate': round(approved / total_reviewed * 100, 1) if total_reviewed else 0
        }

    output = {
        'generated_at': datetime.now().isoformat(),
        'source': 'QA Cosmos DB',
        'entities': entities,
        'tax_years': [int(y) for y in tax_years if y],
        'summary': {
            'total_documents': len(enriched_documents),
            'total_entities': len(entities),
            'total_trcs': sum(d['total_trcs'] for d in enriched_documents),
            'total_changes': len(trcs),  # all detected changes incl. NOT_TRC
            'first_review_completed': total_first_review,
            'trc_desc_acceptance_rate': trc_desc_acceptance,
            'trc_desc_without_comments': trc_desc_no_comment,
            'trc_desc_with_comments': total_first_review - trc_desc_no_comment,
            'acceptance_target': 80,
            'all_field_approval_rate': all_field_approval,
            'approved_fields': approved_fields,
            'total_fields_reviewed': total_fields,
            'field_stats': global_field_stats,
            'tag_analysis': {
                'all_tags': dict(sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)),
                'top_issue_tags': top_issue_tags,
                'top_issue_threshold': round(top_issue_threshold, 1)
            }
        },
        'documents': enriched_documents,
    }

    logging.info(f"Data refresh complete: {len(enriched_documents)} docs, {len(trcs)} TRCs")

    return func.HttpResponse(
        json.dumps(output, default=str),
        status_code=200,
        mimetype="application/json"
    )
