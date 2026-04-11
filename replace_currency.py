import os
import re

def replace_dollar_with_rupee(directory):
    # Regex: Match $ only if followed by a digit or a Django template start {{
    # This avoids breaking jQuery $(...) and JS template literals ${...}
    pattern = re.compile(r'\$(?=\d|\s*\{\{)')
    
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.html'):
                path = os.path.join(root, file)
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                new_content = pattern.sub('₹', content)
                
                if content != new_content:
                    with open(path, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    print(f"Updated: {path}")

if __name__ == "__main__":
    template_dir = os.path.join(os.getcwd(), 'templates')
    replace_dollar_with_rupee(template_dir)
