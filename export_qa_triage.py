"""
QA Triage Dashboard — Export from Cosmos DB
Pulls Documents, TRCs, feedback, and ComparisonJobs from QA Cosmos
to build a JSON file for the QA Triage Dashboard.
"""
from azure.cosmos import CosmosClient
import json
import sys
from datetime import datetime
from collections import defaultdict

# ── Cosmos DB Connection (from environment variables) ──
import os
ENDPOINT = os.environ.get("COSMOS_ENDPOINT", "")
KEY = os.environ.get("COSMOS_KEY", "")
DATABASE = os.environ.get("COSMOS_DATABASE", "tcaDB")
CONTAINER = os.environ.get("COSMOS_CONTAINER", "reg_documents")

if not ENDPOINT or not KEY:
    print("ERROR: COSMOS_ENDPOINT and COSMOS_KEY environment variables must be set.")
    print("Copy .env.example to .env and fill in your credentials, then run:")
    print("  set /a < .env  (Windows) or  export $(cat .env | xargs)  (Linux/Mac)")
    sys.exit(1)

client = CosmosClient(ENDPOINT, KEY)
database = client.get_database_client(DATABASE)
container = database.get_container_client(CONTAINER)

FEEDBACK_FIELDS = ['context', 'system_inference', 'tax_rule_change', 'category']

# ── US Tax Expert Comment Classification ──
TAX_EXPERT_TAGS = {
    'Form Identification Error': {
        'keywords': ['form name', 'form details', 'instructions', 'schedule', 'wrong form'],
        'description': 'Incorrect form name/type or missing form identification details',
        'severity': 'high'
    },
    'Line Reference Inaccuracy': {
        'keywords': ['line', 'reference', 'line number', 'incorrect line', 'wrong line'],
        'description': 'Imprecise or incorrect line number references',
        'severity': 'high'
    },
    'Calculation Change Missing': {
        'keywords': ['calc', 'calculation', 'sum', 'total', 'computation', 'formula'],
        'description': 'Failed to identify computational/formula changes',
        'severity': 'high'
    },
    'Change Type Misclassification': {
        'keywords': ['renumber', 'really a', 'actually', 'misclassif', 'wrong bucket', 'not a'],
        'description': 'Incorrect TRC category or change type assignment',
        'severity': 'high'
    },
    'Context Irrelevance': {
        'keywords': ['irrelevant', 'not relevant', 'wrong context', 'unrelated'],
        'description': 'Context does not match the actual form change',
        'severity': 'medium'
    },
    'Content Verbosity': {
        'keywords': ['wordy', 'too long', 'verbose', 'make it short', 'too much'],
        'description': 'TRC description is unnecessarily lengthy or verbose',
        'severity': 'low'
    },
    'Clarity & Precision': {
        'keywords': ['lacks clarity', 'unclear', 'confusing', 'vague', 'imprecise'],
        'description': 'TRC lacks clear, precise tax language',
        'severity': 'medium'
    },
    'Section Specificity': {
        'keywords': ['section', 'area', 'part', 'clearly defined', 'location', 'where'],
        'description': 'Needs more specific form section/area identification',
        'severity': 'medium'
    },
    'Change Completeness': {
        'keywords': ['missing', 'incomplete', 'both', 'also', 'additional', 'all'],
        'description': 'Missing related changes or incomplete scope coverage',
        'severity': 'high'
    },
    'Unnecessary Content': {
        'keywords': ['unnecessary', 'does not add value', 'remove', 'not needed'],
        'description': 'Content that does not provide tax analysis value',
        'severity': 'low'
    }
}

def classify_tax_comment(comment_text):
    """Classify a comment using US tax expert knowledge"""
    if not comment_text or not comment_text.strip():
        return []
    
    comment_lower = comment_text.lower()
    matched_tags = []
    
    for tag_name, tag_info in TAX_EXPERT_TAGS.items():
        for keyword in tag_info['keywords']:
            if keyword.lower() in comment_lower:
                matched_tags.append({
                    'name': tag_name,
                    'severity': tag_info['severity']
                })
                break
    
    return matched_tags if matched_tags else [{'name': 'General Issue', 'severity': 'medium'}]

print("=" * 70)
print("QA TRIAGE DASHBOARD — COSMOS EXPORT")
print("=" * 70)

# ── 1. Documents ──
print("\n1. Fetching Documents...")
docs_query = """
SELECT c.id, c.document_id, c.document_title, c.document_type,
       c.tax_entity, c.tax_year, c.revision, c.created_at, c.created_by,
       c.forms, c.source
FROM c WHERE c.entity_type = 'Document' AND (c.is_deleted = false OR NOT IS_DEFINED(c.is_deleted))
"""
documents = list(container.query_items(docs_query, enable_cross_partition_query=True))
print(f"   -> {len(documents)} documents")

doc_by_id = {}
for d in documents:
    doc_by_id[d['document_id']] = d

# ── 2. All TRCs ──
print("2. Fetching TaxRuleChange records...")
trcs_query = """
SELECT c.id, c.job_id, c.document_id, c.title, c.classification,
       c.change_type, c.bucket, c.trc_description, c.your_reasoning,
       c.description_of_change, c.line_number_and_reference,
       c.review_status, c.approval_status, c.confident,
       c.override, c.feedback,
       c.created_at, c.created_by, c.reviewed_by, c.reviewed_at,
       c.page, c.additional_details_needed
FROM c WHERE c.entity_type = 'TaxRuleChange'
"""
trcs = list(container.query_items(trcs_query, enable_cross_partition_query=True))
print(f"   -> {len(trcs)} TRCs")

# ── 3. Feedbacks ──
print("3. Fetching TaxRuleChangeCustomFeedbacks...")
fb_query = """
SELECT c.id, c.trc_id, c.document_id,
       c.context, c.system_inference, c.tax_rule_change, c.category,
       c.created_at, c.created_by
FROM c WHERE c.entity_type = 'TaxRuleChangeCustomFeedbacks'
"""
feedbacks = list(container.query_items(fb_query, enable_cross_partition_query=True))
print(f"   -> {len(feedbacks)} feedback records")

feedback_by_trc = {f['trc_id']: f for f in feedbacks}


# ── 4. ComparisonJobs (for review_status context) ──
print("4. Fetching ComparisonJobs...")
jobs_query = """
SELECT c.id, c.job_id, c.document_id, c.compared_doc_id,
       c.created_by, c.created_at, c.tcat_work_item
FROM c WHERE c.entity_type = 'ComparisonJob'
"""
comp_jobs = list(container.query_items(jobs_query, enable_cross_partition_query=True))
print(f"   -> {len(comp_jobs)} comparison jobs")

# ── 5. TaxRuleChangeJob (linking TRC → ComparisonJob) ──
print("5. Fetching TaxRuleChangeJob links...")
trcjob_query = """
SELECT c.job_id, c.comparison_job_id, c.document_id
FROM c WHERE c.entity_type = 'TaxRuleChangeJob'
"""
trc_jobs = list(container.query_items(trcjob_query, enable_cross_partition_query=True))
trc_job_to_comp = {tj['job_id']: tj['comparison_job_id'] for tj in trc_jobs}
comp_job_map = {j['job_id']: j for j in comp_jobs}
print(f"   -> {len(trc_jobs)} links")

# ═══════════════════════════════════════════════════════════════════════
# Build per-entity, per-document enriched TRC list
# ═══════════════════════════════════════════════════════════════════════
print("\n6. Building enriched data...")

# Collect unique entities & tax years
entities = sorted(set(d.get('tax_entity', 'Unknown') for d in documents if d.get('tax_entity')))
tax_years = sorted(set(d.get('tax_year', 0) for d in documents if d.get('tax_year')), reverse=True)

# Group TRCs by document_id
trcs_by_doc = defaultdict(list)
for trc in trcs:
    doc_id = trc.get('document_id', '')
    if doc_id:  # Only group if document_id exists
        trcs_by_doc[doc_id].append(trc)

print(f"   -> TRCs grouped into {len(trcs_by_doc)} documents")

# Build document tracking number from document_id (extract meaningful parts)
def extract_doc_number(doc_id):
    """Extract a reasonable document number from document_id"""
    # Look for patterns like numeric values or meaningful identifiers
    parts = doc_id.split('_')
    # Try to find a meaningful number or create one from hash
    for part in parts:
        if part.isdigit() and len(part) >= 4:
            return part
    # Fallback: create a shorter hash-based number
    return str(hash(doc_id) % 99999).zfill(5)

# Build enriched document list
enriched_documents = []
for doc in documents:
    doc_id = doc['document_id']
    doc_trcs = trcs_by_doc.get(doc_id, [])
    if not doc_trcs:
        continue  # Skip documents with no TRCs

    doc_number = extract_doc_number(doc_id)

    # Enriched TRC list with feedback
    enriched_trcs = []
    for trc in doc_trcs:
        fb = feedback_by_trc.get(trc['id'], {})
        # Apply tax expert classification to comments
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
                enriched_fields[f] = {
                    'status': '', 
                    'comments': '',
                    'tags': []
                }

        # Get actual review status from DB (use actual field, not inferred)
        review_status = trc.get('review_status', 'NotStarted')
        has_feedback = trc['id'] in feedback_by_trc
        
        # Use actual DB review_status if available, otherwise infer from feedback
        if not review_status or review_status == '':
            review_status = 'FirstReviewCompleted' if has_feedback else 'NotStarted'

        # Get job and ADO info
        job_id = trc.get('job_id', '')
        comp_job_id = trc_job_to_comp.get(job_id, '')
        comp_job_info = comp_job_map.get(comp_job_id, {})
        ado_work_item = comp_job_info.get('tcat_work_item', '')
        
        # Extract ADO number from tcat_work_item object
        ado_number = ''
        if ado_work_item:
            if isinstance(ado_work_item, dict) and 'Id' in ado_work_item:
                ado_number = str(ado_work_item['Id'])
            elif isinstance(ado_work_item, (int, str)):
                ado_number = str(ado_work_item)
        
        # Create a short TRC number from ID
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
            'all_tags': list(set(all_tags))  # deduplicated tags for this TRC
        })

    # Count metrics for this document
    # Only count actual TRCs (POTENTIAL_TRC, TRC_REQUIRED) — NOT_TRC are just
    # year-over-year document differences, not real tax-rule changes.
    actual_trcs = [t for t in enriched_trcs if t['classification'] not in ('NOT_TRC', '')]
    total_trcs = len(actual_trcs)
    first_review = [t for t in actual_trcs if t['review_status'] == 'FirstReviewCompleted']
    trcs_with_fb = [t for t in actual_trcs if t['has_feedback']]

    # TRC description approval = TRCs where tax_rule_change field is Approved (or no comment)
    # (computed over actual TRCs only, excluding NOT_TRC)
    trc_desc_approved = 0
    trc_desc_with_comments = 0
    for t in first_review:
        trc_field = t['fields'].get('tax_rule_change', {})
        if trc_field.get('comments'):
            trc_desc_with_comments += 1
        if trc_field.get('status') == 'Approved':
            trc_desc_approved += 1

    # Per-field stats
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
        'total_changes': len(enriched_trcs),      # all detected changes (incl. NOT_TRC)
        'trcs': enriched_trcs,
    })

# ═══════════════════════════════════════════════════════════════════════
# Global summary with tag analysis
# ═══════════════════════════════════════════════════════════════════════
print("7. Computing global summary and tag analysis...")

# Only count actual TRCs (exclude NOT_TRC) for global summary too
all_actual_trcs = [t for d in enriched_documents for t in d['trcs'] if t['classification'] not in ('NOT_TRC', '')]
all_first_review = [t for t in all_actual_trcs if t['review_status'] == 'FirstReviewCompleted']
total_first_review = len(all_first_review)

# Tag frequency analysis
tag_counts = {}
for t in all_first_review:
    for tag in t['all_tags']:
        tag_counts[tag] = tag_counts.get(tag, 0) + 1

# Identify top issue tags (>70% of TRCs)
top_issue_threshold = total_first_review * 0.7
top_issue_tags = {tag: count for tag, count in tag_counts.items() if count > top_issue_threshold} if total_first_review > 0 else {}

# TRC description acceptance: % without comments on tax_rule_change field
trc_desc_no_comment = sum(1 for t in all_first_review if not t['fields'].get('tax_rule_change', {}).get('comments'))
trc_desc_acceptance = round(trc_desc_no_comment / total_first_review * 100, 1) if total_first_review else 0

# All-field approval rate
total_fields = total_first_review * 4
approved_fields = sum(
    1 for t in all_first_review
    for f in FEEDBACK_FIELDS
    if t['fields'].get(f, {}).get('status') == 'Approved'
)
all_field_approval = round(approved_fields / total_fields * 100, 1) if total_fields else 0

# Per-field global
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

summary = {
    'total_documents': len(enriched_documents),
    'total_entities': len(entities),
    'total_trcs': sum(d['total_trcs'] for d in enriched_documents),
    'total_changes': len(trcs),  # all detected changes (incl. NOT_TRC)
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
}

output = {
    'generated_at': datetime.now().isoformat(),
    'source': 'QA Cosmos DB',
    'entities': entities,
    'tax_years': [int(y) for y in tax_years if y],
    'summary': summary,
    'documents': enriched_documents,
}

# ── Write JSON ──
output_file = 'qa_triage_data.json'

# Guard: never overwrite existing data with an empty result.
# QA Cosmos may have no TRCs yet (data cleared / pipeline not run).
# Exit code 2 signals "no data" to server.py without being an error.
if len(trcs) == 0:
    print("Cosmos returned 0 TRCs — keeping existing qa_triage_data.json unchanged.")
    sys.exit(2)
else:
    with open(output_file, 'w', encoding='utf-8') as fp:
        json.dump(output, fp, indent=2, default=str)

print(f"\n{'=' * 70}")
print(f"Exported to {output_file}")
print(f"  Entities:    {', '.join(entities)}")
print(f"  Tax Years:   {tax_years}")
print(f"  Documents:   {len(enriched_documents)} (with TRCs)")
print(f"  Total TRCs:  {len(trcs)}")
print(f"  First Review Completed: {total_first_review}")
print(f"  TRC Desc Acceptance:    {trc_desc_acceptance}% (target 80%)")
print(f"  Top Issue Tags (>{round(top_issue_threshold,1)} TRCs): {list(top_issue_tags.keys())}")
print(f"  All Tag Counts:     {dict(list(tag_counts.items())[:5])}{'...' if len(tag_counts) > 5 else ''}")
print(f"{'=' * 70}")
