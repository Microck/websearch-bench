import os
import shutil
import pandas as pd
import json

def fix_line_endings(filepath):
    with open(filepath, 'r', encoding='utf-8', newline='') as f:
        content = f.read()
    with open(filepath, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content.replace('\r\n', '\n'))

def main():
    source_dir = "/home/ubuntu/workspace/web-search/runs/rep-1-20260808-all8"
    dest_dir = "/home/ubuntu/workspace/web-search/results/rep-1-20260805/raw"

    os.makedirs(dest_dir, exist_ok=True)

    summary = []

    def export_csv(src_rel, dst_rel, drop_cols=None):
        src = os.path.join(source_dir, src_rel)
        dst = os.path.join(dest_dir, dst_rel)
        if not os.path.exists(src):
            print(f"Warning: {src} does not exist.")
            return

        print(f"Exporting CSV: {src_rel} -> {dst_rel}")
        df = pd.read_csv(src)
        if drop_cols:
            df.drop(columns=[c for c in drop_cols if c in df.columns], inplace=True)
            print(f"  Dropped columns: {drop_cols}")
        
        df.to_csv(dst, index=False, lineterminator='\n')
        
        size = os.path.getsize(dst)
        rows = len(df)
        summary.append({'File': dst_rel, 'Size (bytes)': size, 'Rows': rows})

    def export_copy(src_rel, dst_rel):
        src = os.path.join(source_dir, src_rel)
        dst = os.path.join(dest_dir, dst_rel)
        if not os.path.exists(src):
            print(f"Warning: {src} does not exist.")
            return

        print(f"Copying file: {src_rel} -> {dst_rel}")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        fix_line_endings(dst)
        
        size = os.path.getsize(dst)
        
        # Count rows if possible
        try:
            with open(dst, 'r', encoding='utf-8') as f:
                rows = sum(1 for _ in f) - 1 # rough header exclusion if csv
            if not dst.endswith('.csv'):
                rows = "N/A"
        except:
            rows = "N/A"
            
        summary.append({'File': dst_rel, 'Size (bytes)': size, 'Rows': rows})

    def export_json(src_rel, dst_rel, modifier):
        src = os.path.join(source_dir, src_rel)
        dst = os.path.join(dest_dir, dst_rel)
        if not os.path.exists(src):
            print(f"Warning: {src} does not exist.")
            return

        print(f"Exporting JSON: {src_rel} -> {dst_rel}")
        with open(src, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        data = modifier(data)
        
        with open(dst, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, indent=2)
            f.write('\n') # trailing newline
            
        size = os.path.getsize(dst)
        summary.append({'File': dst_rel, 'Size (bytes)': size, 'Rows': 'N/A'})

    # 1. retrieved_results.csv
    export_csv('retrieved_results.csv', 'retrieved_results.csv', drop_cols=['raw_response_path'])

    # 2. runs.csv
    export_csv('runs.csv', 'runs.csv', drop_cols=['request_id', 'raw_response_path', 'cost_usd'])

    # 3. pooled_judgments_primary.csv
    export_copy('pooled_judgments_primary.csv', 'pooled_judgments_primary.csv')

    # 4. quality/scored_results_primary_auto.csv
    export_copy('quality/scored_results_primary_auto.csv', 'scored_results_primary.csv')

    # 5. quality/scored_results_reference_auto.csv
    export_copy('quality/scored_results_reference_auto.csv', 'scored_results_reference.csv')

    # 6. quality/pooled_judgments_reference_auto.csv
    export_copy('quality/pooled_judgments_reference_auto.csv', 'pooled_judgments_reference.csv')

    # 7. quality/ai-judging/pooled_judgments_primary_ai.csv
    export_copy('quality/ai-judging/pooled_judgments_primary_ai.csv', 'pooled_judgments_primary_ai.csv')

    # 8. quality/ai-judging/scored_results_primary_ai.csv
    export_copy('quality/ai-judging/scored_results_primary_ai.csv', 'scored_results_primary_ai.csv')

    # 9. run_manifest.json
    def modify_manifest(data):
        if 'query_file' in data:
            data['query_file'] = 'data/queries.csv'
        return data
    export_json('run_manifest.json', 'run_manifest.json', modify_manifest)

    # 10. run_summary.csv
    export_csv('run_summary.csv', 'run_summary.csv', drop_cols=['total_cost_usd_reported', 'requests_with_reported_cost'])

    # 11. provider_billing_estimates.csv
    export_copy('provider_billing_estimates.csv', 'provider_cost_estimates.csv')

    # 12. quality/judgment-method.md
    export_copy('quality/judgment-method.md', 'judgment-method.md')

    # 13. quality/ai-judging/method.md
    export_copy('quality/ai-judging/method.md', 'ai-judging-method.md')

    print("\n" + "="*50)
    print("EXPORT SUMMARY")
    print("="*50)
    print(f"{'File':<35} | {'Size (bytes)':<15} | {'Rows'}")
    print("-" * 65)
    for s in summary:
        print(f"{s['File']:<35} | {s['Size (bytes)']:<15} | {s['Rows']}")

if __name__ == "__main__":
    main()
