#!/usr/bin/env python3
"""
DAHAO Governance Node (Phase 1 - Parasitic Architecture)

A single-agent governance node that runs in each user's fork via GitHub Actions.
Each node:
1. Reads personal values from its own fork
2. Reads shared law from main repo
3. Participates in governance discussions
4. Votes based on personal values while respecting shared rules

Usage:
    python node.py                    # Auto mode (AI decides)
    python node.py --vote-only        # Only cast votes
    python node.py --respond-only     # Only respond to discussions
"""

import json
import os
import re
import sys
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any
from dataclasses import dataclass


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class NodeConfig:
    """Configuration loaded from environment variables"""
    # Paths
    fork_path: str = os.getenv("FORK_PATH", "./fork")
    main_path: str = os.getenv("MAIN_PATH", "./main")
    
    # GitHub
    main_repo: str = os.getenv("MAIN_REPO", "dahao-org/glitch-economy-simulation")
    github_token: str = os.getenv("GITHUB_TOKEN", os.getenv("GH_PAT", ""))
    
    # LLM (priority order)
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    
    # Node identity (derived from fork)
    node_name: str = os.getenv("NODE_NAME", "Anonymous Node")
    wallet_address: str = os.getenv("WALLET_ADDRESS", "")
    
    # Behavior
    action_mode: str = os.getenv("ACTION_MODE", "auto")
    min_votes_quorum: int = 3
    
    # Gemini model
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-exp")


# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

class Logger:
    def __init__(self, log_file: str = None):
        self.log_file = open(log_file, "w") if log_file else None
        
    def log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = {"INFO": "ℹ️", "SUCCESS": "✅", "WARNING": "⚠️", "ERROR": "❌", "DEBUG": "🔍"}
        formatted = f"[{timestamp}] {prefix.get(level, '')} {message}"
        print(formatted)
        if self.log_file:
            self.log_file.write(formatted + "\n")
            self.log_file.flush()
    
    def close(self):
        if self.log_file:
            self.log_file.close()


# ═══════════════════════════════════════════════════════════════════════════════
# LLM CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

class LLMClient:
    """Multi-provider LLM client with fallback"""
    
    def __init__(self, config: NodeConfig, logger: Logger):
        self.config = config
        self.logger = logger
    
    def generate(self, prompt: str) -> Optional[str]:
        """Generate response using available LLM provider"""
        
        # Try Gemini first
        if self.config.gemini_api_key:
            response = self._call_gemini(prompt)
            if response:
                return response
        
        # Try OpenAI
        if self.config.openai_api_key:
            response = self._call_openai(prompt)
            if response:
                return response
        
        # Try Anthropic
        if self.config.anthropic_api_key:
            response = self._call_anthropic(prompt)
            if response:
                return response
        
        self.logger.log("No LLM API keys configured!", "ERROR")
        return None
    
    def _call_gemini(self, prompt: str) -> Optional[str]:
        """Call Gemini API"""
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.config.gemini_model}:generateContent"
            headers = {"Content-Type": "application/json"}
            params = {"key": self.config.gemini_api_key}
            data = {"contents": [{"parts": [{"text": prompt}]}]}
            
            response = requests.post(url, headers=headers, params=params, json=data, timeout=60)
            
            if response.status_code == 200:
                result = response.json()
                return result["candidates"][0]["content"]["parts"][0]["text"]
            elif response.status_code == 429:
                self.logger.log("Gemini rate limited", "WARNING")
            else:
                self.logger.log(f"Gemini error: {response.status_code}", "ERROR")
        except Exception as e:
            self.logger.log(f"Gemini exception: {e}", "ERROR")
        return None
    
    def _call_openai(self, prompt: str) -> Optional[str]:
        """Call OpenAI API"""
        try:
            url = "https://api.openai.com/v1/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.openai_api_key}"
            }
            data = {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2000
            }
            
            response = requests.post(url, headers=headers, json=data, timeout=60)
            
            if response.status_code == 200:
                result = response.json()
                return result["choices"][0]["message"]["content"]
            else:
                self.logger.log(f"OpenAI error: {response.status_code}", "ERROR")
        except Exception as e:
            self.logger.log(f"OpenAI exception: {e}", "ERROR")
        return None
    
    def _call_anthropic(self, prompt: str) -> Optional[str]:
        """Call Anthropic API"""
        try:
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "Content-Type": "application/json",
                "x-api-key": self.config.anthropic_api_key,
                "anthropic-version": "2023-06-01"
            }
            data = {
                "model": "claude-3-haiku-20240307",
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": prompt}]
            }
            
            response = requests.post(url, headers=headers, json=data, timeout=60)
            
            if response.status_code == 200:
                result = response.json()
                return result["content"][0]["text"]
            else:
                self.logger.log(f"Anthropic error: {response.status_code}", "ERROR")
        except Exception as e:
            self.logger.log(f"Anthropic exception: {e}", "ERROR")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# GITHUB CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

class GitHubClient:
    """GitHub API client for discussions"""
    
    def __init__(self, config: NodeConfig, logger: Logger):
        self.config = config
        self.logger = logger
        self.repo_id = None
        self.category_ids = {}
    
    def _graphql(self, query: str, variables: Dict = None) -> Optional[Dict]:
        """Execute GraphQL query"""
        url = "https://api.github.com/graphql"
        headers = {
            "Authorization": f"Bearer {self.config.github_token}",
            "Content-Type": "application/json"
        }
        data = {"query": query}
        if variables:
            data["variables"] = variables
        
        try:
            response = requests.post(url, headers=headers, json=data, timeout=30)
            if response.status_code == 200:
                return response.json()
            else:
                self.logger.log(f"GitHub API error: {response.status_code}", "ERROR")
        except Exception as e:
            self.logger.log(f"GitHub exception: {e}", "ERROR")
        return None
    
    def ensure_repo_info(self):
        """Fetch repo ID and category IDs"""
        if self.repo_id:
            return
        
        owner, name = self.config.main_repo.split("/")
        query = '''
        query($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) {
            id
            discussionCategories(first: 20) {
              nodes { id name }
            }
          }
        }
        '''
        
        result = self._graphql(query, {"owner": owner, "name": name})
        if result and "data" in result:
            repo = result["data"]["repository"]
            self.repo_id = repo["id"]
            for cat in repo["discussionCategories"]["nodes"]:
                self.category_ids[cat["name"]] = cat["id"]
            self.logger.log(f"Connected to {self.config.main_repo}", "SUCCESS")
    
    def get_discussions(self) -> List[Dict]:
        """Fetch active discussions"""
        self.ensure_repo_info()
        owner, name = self.config.main_repo.split("/")
        
        query = '''
        query($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) {
            discussions(first: 20, orderBy: {field: UPDATED_AT, direction: DESC}) {
              nodes {
                id
                number
                title
                author { login }
                body
                createdAt
                comments(first: 50) {
                  nodes {
                    body
                    author { login }
                  }
                }
              }
            }
          }
        }
        '''
        
        result = self._graphql(query, {"owner": owner, "name": name})
        if not result or "data" not in result:
            return []
        
        discussions = []
        for node in result["data"]["repository"]["discussions"]["nodes"]:
            comments = []
            for c in node["comments"]["nodes"]:
                comments.append({
                    "body": c["body"],
                    "author": c["author"]["login"] if c["author"] else "Unknown"
                })
            
            discussions.append({
                "id": node["id"],
                "number": node["number"],
                "title": node["title"],
                "author": node["author"]["login"] if node["author"] else "Unknown",
                "body": node["body"],
                "comments": comments,
                "createdAt": node["createdAt"]
            })
        
        return discussions
    
    def post_comment(self, discussion_id: str, body: str) -> bool:
        """Post a comment to a discussion"""
        mutation = '''
        mutation($discussionId: ID!, $body: String!) {
          addDiscussionComment(input: {discussionId: $discussionId, body: $body}) {
            comment { id }
          }
        }
        '''
        
        result = self._graphql(mutation, {"discussionId": discussion_id, "body": body})
        if result and "data" in result:
            self.logger.log(f"Posted comment", "SUCCESS")
            return True
        return False
    
    def create_discussion(self, title: str, body: str, category: str = "General") -> Optional[Dict]:
        """Create a new discussion"""
        self.ensure_repo_info()
        
        category_id = self.category_ids.get(category)
        if not category_id:
            # Fallback to first available
            category_id = list(self.category_ids.values())[0] if self.category_ids else None
        
        if not category_id:
            self.logger.log("No discussion categories found", "ERROR")
            return None
        
        mutation = '''
        mutation($repoId: ID!, $categoryId: ID!, $title: String!, $body: String!) {
          createDiscussion(input: {repositoryId: $repoId, categoryId: $categoryId, title: $title, body: $body}) {
            discussion { id number url }
          }
        }
        '''
        
        result = self._graphql(mutation, {
            "repoId": self.repo_id,
            "categoryId": category_id,
            "title": title,
            "body": body
        })
        
        if result and "data" in result:
            disc = result["data"]["createDiscussion"]["discussion"]
            self.logger.log(f"Created discussion #{disc['number']}", "SUCCESS")
            return disc
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# GOVERNANCE NODE
# ═══════════════════════════════════════════════════════════════════════════════

class GovernanceNode:
    """Main governance node that participates in DAHAO governance"""
    
    def __init__(self, config: NodeConfig):
        self.config = config
        self.logger = Logger(f"node_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
        self.llm = LLMClient(config, self.logger)
        self.github = GitHubClient(config, self.logger)
        
        # Load contexts
        self.fork_context = self._load_fork_context()
        self.main_context = self._load_main_context()
        self.available_refs = self._build_available_refs()
    
    def _load_json(self, path: Path) -> Dict:
        """Load JSON file"""
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            self.logger.log(f"Failed to load {path}: {e}", "ERROR")
            return {}
    
    def _load_fork_context(self) -> Dict[str, Any]:
        """Load personal values from fork"""
        base = Path(self.config.fork_path) / "data"
        return {
            "terms": self._load_json(base / "terms.json"),
            "principles": self._load_json(base / "principles.json"),
            "rules": self._load_json(base / "rules.json")
        }
    
    def _load_main_context(self) -> Dict[str, Any]:
        """Load shared law from main repo"""
        base = Path(self.config.main_path) / "data"
        return {
            "terms": self._load_json(base / "terms.json"),
            "principles": self._load_json(base / "principles.json"),
            "rules": self._load_json(base / "rules.json"),
            "governance": self._load_json(base / "governance.json")
        }
    
    def _build_available_refs(self) -> Dict[str, set]:
        """Build set of available @references from main repo"""
        refs = {"terms": set(), "principles": set(), "rules": set()}
        for file_type in refs.keys():
            data = self.main_context.get(file_type, {})
            refs[file_type].update(k for k in data.keys() if k.startswith("@"))
        return refs
    
    def _get_fork_values_with_definitions(self) -> str:
        """Get fork values that differ from main, with definitions"""
        values = []
        
        for file_type in ["terms", "principles"]:
            fork_data = self.fork_context.get(file_type, {})
            main_data = self.main_context.get(file_type, {})
            
            for key in fork_data:
                if key.startswith("@") and not key.startswith("@_") and key not in main_data:
                    val = fork_data[key]
                    definition = ""
                    
                    if isinstance(val, dict):
                        definition = val.get("definition", val.get("summary", val.get("description", "")))
                    elif isinstance(val, str) and val.strip():
                        definition = val
                    
                    if not definition or not definition.strip():
                        continue
                    
                    if len(definition) > 100:
                        definition = definition[:100] + "..."
                    
                    values.append(f"• {key}: \"{definition}\"")
        
        return "\n".join(values) if values else "• (aligned with main repo)"
    
    def _get_main_summary(self) -> str:
        """Get summary of main repo values"""
        terms = list(self.available_refs.get("terms", set()))[:10]
        principles = list(self.available_refs.get("principles", set()))[:10]
        return f"Terms: {', '.join(terms)}\nPrinciples: {', '.join(principles)}"
    
    def _determine_phase(self, discussion: Dict) -> str:
        """Determine discussion phase"""
        body = discussion.get("body", "")
        comments = discussion.get("comments", [])
        
        has_thesis = "[THESIS]" in body
        has_antithesis = any("[ANTITHESIS]" in c.get("body", "") for c in comments)
        has_synthesis = any("[SYNTHESIS]" in c.get("body", "") for c in comments)
        has_votes = any("**VOTE:" in c.get("body", "") for c in comments)
        
        if has_votes:
            return "VOTING"
        elif has_synthesis:
            return "SYNTHESIS"
        elif has_antithesis:
            return "ANTITHESIS"
        elif has_thesis:
            return "THESIS"
        return "UNKNOWN"
    
    def _count_votes(self, discussion: Dict) -> Dict[str, int]:
        """Count votes in discussion"""
        votes = {"APPROVE": 0, "REJECT": 0, "ABSTAIN": 0}
        pattern = r'\*\*VOTE:\s*(APPROVE|REJECT|ABSTAIN)\*\*'
        voters = {}
        
        for comment in discussion.get("comments", []):
            author = comment.get("author", "")
            body = comment.get("body", "")
            matches = re.findall(pattern, body)
            if matches:
                voters[author] = matches[-1]
        
        for vote in voters.values():
            votes[vote] += 1
        
        return votes
    
    def _build_prompt(self, discussions: List[Dict]) -> str:
        """Build prompt for LLM decision"""
        fork_values = self._get_fork_values_with_definitions()
        main_summary = self._get_main_summary()
        
        # Format discussions
        disc_text = ""
        for d in discussions[:5]:  # Limit to 5 most recent
            phase = self._determine_phase(d)
            votes = self._count_votes(d)
            
            disc_text += f"\n### #{d['number']} [{phase}] {d['title'][:50]}\n"
            disc_text += f"Author: {d['author']} | Votes: {votes['APPROVE']}A/{votes['REJECT']}R\n"
            disc_text += f"Body: {d['body'][:500]}...\n"
            
            for c in d.get("comments", [])[-3:]:  # Last 3 comments
                disc_text += f"- @{c['author']}: {c['body'][:200]}...\n"
        
        return f'''You are a DAHAO Governance Node participating in democratic governance.

═══════════════════════════════════════════════════════════════════
YOUR IDENTITY (Fork Values - Your Personal Beliefs)
═══════════════════════════════════════════════════════════════════
{fork_values}

═══════════════════════════════════════════════════════════════════
SHARED LAW (Main Repo - What You Can Reference)
═══════════════════════════════════════════════════════════════════
{main_summary}

═══════════════════════════════════════════════════════════════════
CURRENT DISCUSSIONS
═══════════════════════════════════════════════════════════════════
{disc_text}

═══════════════════════════════════════════════════════════════════
RULES
═══════════════════════════════════════════════════════════════════
1. You can CREATE_PROPOSAL, POST_ANTITHESIS, POST_SYNTHESIS, CAST_VOTE, or DO_NOTHING
2. Proposals MUST include **PROPOSED DEFINITION** with JSON
3. Votes use format: **VOTE: APPROVE** or **VOTE: REJECT**
4. Only reference @terms that exist in MAIN REPO
5. Your fork values motivate WHY, but definitions use shared terms

═══════════════════════════════════════════════════════════════════
RESPOND WITH JSON ONLY
═══════════════════════════════════════════════════════════════════
{{
  "reasoning": "Why you chose this action",
  "action": "CREATE_PROPOSAL|POST_ANTITHESIS|POST_SYNTHESIS|CAST_VOTE|DO_NOTHING",
  "target_discussion_id": "discussion node ID if responding",
  "target_number": discussion number if responding,
  "title": "Title if creating proposal",
  "content": "Full content to post",
  "vote": "APPROVE|REJECT|ABSTAIN if voting"
}}'''
    
    def run(self):
        """Main execution loop"""
        self.logger.log("═" * 50)
        self.logger.log("DAHAO GOVERNANCE NODE")
        self.logger.log("═" * 50)
        self.logger.log(f"Mode: {self.config.action_mode}")
        self.logger.log(f"Fork values: {self._get_fork_values_with_definitions()[:100]}...")
        
        # Get current discussions
        discussions = self.github.get_discussions()
        self.logger.log(f"Found {len(discussions)} active discussions")
        
        if not discussions and self.config.action_mode != "propose":
            self.logger.log("No discussions to act on", "WARNING")
            return
        
        # Build prompt and get decision
        prompt = self._build_prompt(discussions)
        self.logger.log(f"Prompt size: {len(prompt)} chars")
        
        response = self.llm.generate(prompt)
        if not response:
            self.logger.log("Failed to get LLM response", "ERROR")
            return
        
        # Parse decision
        try:
            json_match = re.search(r'\{[\s\S]*\}', response)
            if not json_match:
                self.logger.log("No JSON in response", "ERROR")
                return
            
            decision = json.loads(json_match.group())
            self.logger.log(f"Decision: {decision.get('action', 'UNKNOWN')}")
            
        except json.JSONDecodeError as e:
            self.logger.log(f"JSON parse error: {e}", "ERROR")
            return
        
        # Execute action
        self._execute_action(decision)
        
        self.logger.log("═" * 50)
        self.logger.log("NODE COMPLETE")
        self.logger.log("═" * 50)
        self.logger.close()
    
    def _execute_action(self, decision: Dict):
        """Execute the decided action"""
        action = decision.get("action", "DO_NOTHING")
        
        # Build fork header
        fork_values = self._get_fork_values_with_definitions()
        fork_header = f"📌 **MY FORK VALUES:**\n{fork_values}\n\n---\n\n"
        
        # Check if content already has header
        content = decision.get("content", "")
        if "MY FORK VALUES" in content or "📌" in content:
            fork_header = ""
        
        if action == "CREATE_PROPOSAL":
            title = decision.get("title", "Untitled Proposal")
            full_content = fork_header + content
            self.github.create_discussion(title, full_content)
        
        elif action in ["POST_ANTITHESIS", "POST_SYNTHESIS"]:
            disc_id = decision.get("target_discussion_id")
            if disc_id:
                full_content = fork_header + content
                self.github.post_comment(disc_id, full_content)
            else:
                self.logger.log("No target discussion ID", "ERROR")
        
        elif action == "CAST_VOTE":
            disc_id = decision.get("target_discussion_id")
            vote = decision.get("vote", "ABSTAIN")
            reasoning = decision.get("reasoning", "")
            
            vote_content = fork_header
            vote_content += f"**VOTE: {vote}**\n\n"
            vote_content += f"*{reasoning}*"
            
            if disc_id:
                self.github.post_comment(disc_id, vote_content)
            else:
                self.logger.log("No target discussion ID", "ERROR")
        
        elif action == "DO_NOTHING":
            self.logger.log("Chose to do nothing")
        
        else:
            self.logger.log(f"Unknown action: {action}", "WARNING")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="DAHAO Governance Node")
    parser.add_argument("--vote-only", action="store_true", help="Only cast votes")
    parser.add_argument("--respond-only", action="store_true", help="Only respond to discussions")
    parser.add_argument("--propose", action="store_true", help="Create new proposal")
    args = parser.parse_args()
    
    # Override action mode from args
    config = NodeConfig()
    if args.vote_only:
        config.action_mode = "vote_only"
    elif args.respond_only:
        config.action_mode = "respond"
    elif args.propose:
        config.action_mode = "propose"
    
    node = GovernanceNode(config)
    node.run()


if __name__ == "__main__":
    main()
