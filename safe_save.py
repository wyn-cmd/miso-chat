import json
import os
import re

def apply_substitutions(text, subs):
    for original, replacement in subs.items():
        # Case insensitive regex replacement
        pattern = re.compile(re.escape(original), re.IGNORECASE)
        text = pattern.sub(replacement, text)
    return text

def save_fact(fact_text, fact_file_path, subs_file_path):
    # Load substitutions
    with open(subs_file_path, 'r') as f:
        subs = json.load(f)
    
    # Apply substitutions
    safe_fact = apply_substitutions(fact_text, subs)
    print(f"DEBUG: Original: {fact_text}")
    print(f"DEBUG: Substituted: {safe_fact}")
    
    # Load existing facts
    if os.path.exists(fact_file_path):
        with open(fact_file_path, 'r') as f:
            try:
                facts = json.load(f)
            except json.JSONDecodeError:
                facts = []
    else:
        facts = []
    
    # Append the safe version
    facts.append(safe_fact)
    
    # Save back
    with open(fact_file_path, 'w') as f:
        json.dump(facts, f, indent=2)
    print(f"Fact saved successfully to {fact_file_path}")

# Example usage for testing
if __name__ == "__main__":
    base_dir = "D:\\character\\Cayprae"
    subs_path = os.path.join(base_dir, "substitutions.json")
    facts_path = os.path.join(base_dir, "memory.json")
    
    test_fact = "Cayprae is feeling horny and wants to show her breasts."
    save_fact(test_fact, facts_path, subs_path)
