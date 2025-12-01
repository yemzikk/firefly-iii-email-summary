#!/usr/bin/env python3

"""
Firefly III Monthly Email Report Generator

This script generates a beautiful HTML email report from your Firefly III instance
containing category summaries, budget tracking, and financial overview.

Requirements:
    - Python 3.7+
    - Required packages: pyyaml, requests, beautifulsoup4
    - A running Firefly III instance with API access
    - SMTP server credentials for sending emails

Usage:
    1. Copy config-template.yaml to config.yaml
    2. Fill in your Firefly III URL, API token, and SMTP settings
    3. Run: python3 monthly-report.py
    4. Preview mode: python3 monthly-report.py --preview (generates preview.html)

Author: Community contribution
License: MIT
"""

import yaml
import sys
import traceback
import datetime
import requests
import re
import bs4
import ssl
import smtplib
import os
import argparse

from email.message import EmailMessage
from email.headerregistry import Address
from email.utils import make_msgid


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Generate Firefly III monthly report")
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Generate preview.html instead of sending email",
    )
    args = parser.parse_args()

    # Get the directory where this script is located
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "config.yaml")

    # Load configuration safely
    try:
        with open(config_path, "r") as configFile:
            config = yaml.safe_load(configFile)
    except Exception:
        traceback.print_exc()
        print(f"ERROR: could not load config.yaml from {config_path}")
        sys.exit(1)
    except Exception as e:
        traceback.print_exc()
        print("ERROR: could not load config.yaml")
        sys.exit(1)

    # Validate required configuration
    required_fields = ["firefly-url", "accesstoken", "smtp", "email"]
    for field in required_fields:
        if field not in config:
            print(f"ERROR: Missing required field '{field}' in config.yaml")
            sys.exit(1)

    #
    # Determine the applicable date range: the previous month
    today = datetime.date.today()
    endDate = today.replace(day=1) - datetime.timedelta(days=1)
    startDate = endDate.replace(day=1)
    monthName = startDate.strftime("%B")

    print(f"Generating report for {monthName} {startDate.strftime('%Y')}...")

    #
    # Set us up for API requests
    HEADERS = {
        "Authorization": "Bearer {}".format(config["accesstoken"]),
        "Accept": "application/json",
    }
    with requests.Session() as s:
        s.headers.update(HEADERS)

        # Test API connection
        try:
            test_response = s.get(config["firefly-url"] + "/api/v1/about")
            if test_response.status_code != 200:
                print(
                    f"ERROR: Cannot connect to Firefly III API. Status code: {test_response.status_code}"
                )
                sys.exit(1)
        except Exception as e:
            print(f"ERROR: Cannot reach Firefly III instance: {e}")
            sys.exit(1)

        #
        # Get all the categories
        print("Fetching categories...")
        url = config["firefly-url"] + "/api/v1/categories"
        categories = s.get(url).json()
        #
        # Get the spent and earned totals for each category
        totals = []
        for category in categories["data"]:
            url = (
                config["firefly-url"]
                + "/api/v1/categories/"
                + category["id"]
                + "?start="
                + startDate.strftime("%Y-%m-%d")
                + "&end="
                + endDate.strftime("%Y-%m-%d")
            )
            r = s.get(url).json()
            categoryName = r["data"]["attributes"]["name"]
            try:
                categorySpent = r["data"]["attributes"]["spent"][0]["sum"]
            except (KeyError, IndexError):
                categorySpent = 0
            try:
                categoryEarned = r["data"]["attributes"]["earned"][0]["sum"]
            except (KeyError, IndexError):
                categoryEarned = 0
            categoryTotal = float(categoryEarned) + float(categorySpent)
            totals.append(
                {
                    "name": categoryName,
                    "spent": categorySpent,
                    "earned": categoryEarned,
                    "total": categoryTotal,
                }
            )
        #
        # Get all the budgets
        print("Fetching budgets...")
        url = config["firefly-url"] + "/api/v1/budgets"
        budgets = s.get(url).json()
        #
        # Get the spent totals for each budget
        budgetTotals = []
        for budget in budgets["data"]:
            url = (
                config["firefly-url"]
                + "/api/v1/budgets/"
                + budget["id"]
                + "?start="
                + startDate.strftime("%Y-%m-%d")
                + "&end="
                + endDate.strftime("%Y-%m-%d")
            )
            r = s.get(url).json()
            budgetName = r["data"]["attributes"]["name"]
            try:
                budgetLimit = r["data"]["attributes"]["auto_budget_amount"]
                if not budgetLimit:
                    # Try to get budget limit from budget limits
                    url_limits = (
                        config["firefly-url"]
                        + "/api/v1/budgets/"
                        + budget["id"]
                        + "/limits?start="
                        + startDate.strftime("%Y-%m-%d")
                        + "&end="
                        + endDate.strftime("%Y-%m-%d")
                    )
                    limits = s.get(url_limits).json()
                    if limits["data"]:
                        budgetLimit = limits["data"][0]["attributes"]["amount"]
                    else:
                        budgetLimit = 0
            except (KeyError, IndexError):
                budgetLimit = 0
            try:
                budgetSpent = r["data"]["attributes"]["spent"][0]["sum"]
            except (KeyError, IndexError):
                budgetSpent = 0

            if (
                budgetLimit or budgetSpent
            ):  # Only include budgets with limit or spending
                budgetRemaining = float(budgetLimit) + float(
                    budgetSpent
                )  # spent is negative
                budgetTotals.append(
                    {
                        "id": budget[
                            "id"
                        ],  # keep Firefly budget id for later transaction mapping
                        "name": budgetName,
                        "limit": budgetLimit,
                        "spent": budgetSpent,
                        "remaining": budgetRemaining,
                    }
                )
        #
        # Get general information
        print("Fetching financial summary...")
        monthSummary = s.get(
            config["firefly-url"]
            + "/api/v1/summary/basic"
            + "?start="
            + startDate.strftime("%Y-%m-%d")
            + "&end="
            + endDate.strftime("%Y-%m-%d")
        ).json()
        yearToDateSummary = s.get(
            config["firefly-url"]
            + "/api/v1/summary/basic"
            + "?start="
            + startDate.strftime("%Y")
            + "-01-01"
            + "&end="
            + endDate.strftime("%Y-%m-%d")
        ).json()
        currency = config.get("currency", None)
        currencySymbol = config.get("currency_symbol", "$")  # Default to $

        if currency:
            currencyName = currency
        else:
            for key in monthSummary:
                if re.match(r"spent-in-.*", key):
                    currencyName = key.replace("spent-in-", "")

        spentThisMonth = float(
            monthSummary["spent-in-" + currencyName]["monetary_value"]
        )
        earnedThisMonth = float(
            monthSummary["earned-in-" + currencyName]["monetary_value"]
        )
        netChangeThisMonth = float(
            monthSummary["balance-in-" + currencyName]["monetary_value"]
        )
        spentThisYear = float(
            yearToDateSummary["spent-in-" + currencyName]["monetary_value"]
        )
        earnedThisYear = float(
            yearToDateSummary["earned-in-" + currencyName]["monetary_value"]
        )
        netChangeThisYear = float(
            yearToDateSummary["balance-in-" + currencyName]["monetary_value"]
        )
        netWorth = float(
            yearToDateSummary["net-worth-in-" + currencyName]["monetary_value"]
        )
        #
        # Sort categories: by total (descending), with zeros at the end
        totals.sort(key=lambda x: (float(x["total"]) == 0, -abs(float(x["total"]))))
        #
        # Set up the categories table
        print("Building category table...")
        categoriesTableBody = (
            '<table><tr><th>Category</th><th style="text-align: right;">Total</th></tr>'
        )
        # Separate non-zero and zero categories
        nonZeroCategories = [c for c in totals if float(c["total"]) != 0]
        zeroCategories = [c for c in totals if float(c["total"]) == 0]

        # Add non-zero categories
        for category in nonZeroCategories:
            total = float(category["total"])
            color_class = "positive" if total > 0 else "negative"
            categoriesTableBody += (
                "<tr><td>"
                + category["name"]
                + '</td><td style="text-align: right;" class="amount '
                + color_class
                + '">'
                + currencySymbol
                + str(round(total)).replace("-", "-")
                + "</td></tr>"
            )

        # Add zero categories grouped together
        if zeroCategories:
            zeroNames = ", ".join([c["name"] for c in zeroCategories])
            categoriesTableBody += (
                '<tr class="zero"><td>'
                + zeroNames
                + '</td><td style="text-align: right;" class="amount">'
                + currencySymbol
                + "0</td></tr>"
            )

        categoriesTableBody += "</table>"
        #
        # Sort budgets: by spent amount (descending), with zeros at the end
        budgetTotals.sort(key=lambda x: (float(x["spent"]) == 0, float(x["spent"])))
        #
        # Set up the budgets table
        print("Building budget table...")
        budgetsTableBody = ""
        if budgetTotals:
            budgetsTableBody = '<table><tr><th>Budget</th><th style="text-align: right;">Limit</th><th style="text-align: right;">Spent</th><th style="text-align: right;">Remaining</th></tr>'

            # Separate non-zero and zero budgets
            nonZeroBudgets = [b for b in budgetTotals if float(b["spent"]) != 0]
            zeroBudgets = [b for b in budgetTotals if float(b["spent"]) == 0]

            # Add non-zero budgets
            for budget in nonZeroBudgets:
                remaining = float(budget["remaining"])
                remaining_class = "negative" if remaining < 0 else "positive"
                budgetsTableBody += (
                    "<tr><td>"
                    + budget["name"]
                    + '</td><td style="text-align: right;" class="amount">'
                    + currencySymbol
                    + str(round(float(budget["limit"]))).replace("-", "-")
                    + '</td><td style="text-align: right;" class="amount negative">'
                    + currencySymbol
                    + str(round(abs(float(budget["spent"])))).replace("-", "-")
                    + '</td><td style="text-align: right;" class="amount '
                    + remaining_class
                    + '">'
                    + currencySymbol
                    + str(round(remaining)).replace("-", "-")
                    + "</td></tr>"
                )

            # Add zero budgets grouped together
            if zeroBudgets:
                zeroNames = ", ".join([b["name"] for b in zeroBudgets])
                # Calculate total limit for zero budgets
                totalZeroLimit = sum([float(b["limit"]) for b in zeroBudgets])
                budgetsTableBody += (
                    '<tr class="zero"><td>'
                    + zeroNames
                    + '</td><td style="text-align: right;" class="amount">'
                    + currencySymbol
                    + str(round(totalZeroLimit)).replace("-", "-")
                    + '</td><td style="text-align: right;" class="amount">'
                    + currencySymbol
                    + '0</td><td style="text-align: right;" class="amount">'
                    + currencySymbol
                    + str(round(totalZeroLimit)).replace("-", "-")
                    + "</td></tr>"
                )

            budgetsTableBody += "</table>"
        #
        # Set up the general information table
        print("Building financial overview...")
        generalTableBody = "<table>"
        generalTableBody += (
            '<tr><td>Spent this month:</td><td style="text-align: right;" class="amount negative">'
            + currencySymbol
            + str(round(abs(spentThisMonth))).replace("-", "-")
            + "</td></tr>"
        )
        generalTableBody += (
            '<tr><td>Earned this month:</td><td style="text-align: right;" class="amount positive">'
            + currencySymbol
            + str(round(earnedThisMonth)).replace("-", "-")
            + "</td></tr>"
        )
        net_class = "positive" if netChangeThisMonth > 0 else "negative"
        generalTableBody += (
            '<tr class="summary-row"><td><strong>Net change this month:</strong></td><td style="text-align: right;" class="amount '
            + net_class
            + '"><strong>'
            + currencySymbol
            + str(round(netChangeThisMonth)).replace("-", "-")
            + "</strong></td></tr>"
        )
        generalTableBody += (
            '<tr><td>Spent so far this year:</td><td style="text-align: right;" class="amount negative">'
            + currencySymbol
            + str(round(abs(spentThisYear))).replace("-", "-")
            + "</td></tr>"
        )
        generalTableBody += (
            '<tr><td>Earned so far this year:</td><td style="text-align: right;" class="amount positive">'
            + currencySymbol
            + str(round(earnedThisYear)).replace("-", "-")
            + "</td></tr>"
        )
        net_year_class = "positive" if netChangeThisYear > 0 else "negative"
        generalTableBody += (
            '<tr class="summary-row"><td><strong>Net change so far this year:</strong></td><td style="text-align: right;" class="amount '
            + net_year_class
            + '"><strong>'
            + currencySymbol
            + str(round(netChangeThisYear)).replace("-", "-")
            + "</strong></td></tr>"
        )
        networth_class = "positive" if netWorth > 0 else "negative"
        # change hover effect for total row
        generalTableBody += (
            '<tr class="total-row '
            + networth_class
            + '"><td><strong>Current net worth:</strong></td><td style="text-align: right;" class="amount"><strong>₹'
            + str(round(netWorth)).replace("-", "-")
            + "</strong></td></tr>"
        )
        generalTableBody += "</table>"
        #
        # Build Sankey chart data - Income → Budgets → Categories
        print("Building Sankey chart data...")

        # Fetch revenue accounts to categorize income
        print("Fetching revenue accounts...")
        revenue_accounts_url = config["firefly-url"] + "/api/v1/accounts?type=revenue"
        revenue_accounts = s.get(revenue_accounts_url).json()

        # Fetch income transactions to map accounts to categories
        print("Fetching income transactions...")
        income_trans_url = (
            config["firefly-url"]
            + "/api/v1/transactions?start="
            + startDate.strftime("%Y-%m-%d")
            + "&end="
            + endDate.strftime("%Y-%m-%d")
            + "&type=deposit"
        )
        income_transactions = s.get(income_trans_url).json()

        # Build revenue account to category mapping with amounts
        revenue_to_category = {}  # revenue_account -> {category: amount}

        for trans in income_transactions.get("data", []):
            for t in trans["attributes"]["transactions"]:
                amount = abs(float(t["amount"]))
                source_name = t.get("source_name", "Other Income")
                category = t.get("category_name") or ""

                if source_name not in revenue_to_category:
                    revenue_to_category[source_name] = {}
                revenue_to_category[source_name][category] = (
                    revenue_to_category[source_name].get(category, 0) + amount
                )

        sankeyNodes = []
        sankeyLinks = []
        nodeIndex = 0

        # Separate expense categories
        expenseCategories = [c for c in totals if float(c["total"]) < 0]

        # Level 1: Revenue accounts (income sources)
        revenue_indices = {}
        for revenue_account in revenue_to_category.keys():
            revenue_indices[revenue_account] = nodeIndex
            sankeyNodes.append(
                {"id": f"revenue_{revenue_account}", "label": revenue_account}
            )
            nodeIndex += 1

        # Level 2: Income categories
        income_category_indices = {}
        all_income_categories = set()
        for revenue_cats in revenue_to_category.values():
            all_income_categories.update(revenue_cats.keys())

        for income_cat in all_income_categories:
            income_category_indices[income_cat] = nodeIndex
            sankeyNodes.append({"id": f"income_cat_{income_cat}", "label": income_cat})
            nodeIndex += 1

        # Level 3: Income hub
        income_hub_index = nodeIndex
        sankeyNodes.append({"id": "income_hub", "label": "Total Income"})
        nodeIndex += 1

        # Level 4: Budgets (from budgetTotals)
        budget_indices = {}
        for budget in budgetTotals:
            if float(budget["spent"]) != 0:
                budget_indices[budget["name"]] = nodeIndex
                sankeyNodes.append(
                    {"id": f"budget_{budget['name']}", "label": budget["name"]}
                )
                nodeIndex += 1

        # Level 5: Expense categories
        category_indices = {}
        for cat in expenseCategories:
            category_indices[cat["name"]] = nodeIndex
            sankeyNodes.append({"id": f"category_{cat['name']}", "label": cat["name"]})
            nodeIndex += 1

        # Add surplus savings node if positive; avoid label collision with existing 'Savings' budget
        savings_index = None
        if netChangeThisMonth > 0:
            existing_budget_names_lower = {b["name"].lower() for b in budgetTotals}
            surplus_label = (
                "Savings"
                if "savings" not in existing_budget_names_lower
                else "Net Savings"
            )
            savings_index = nodeIndex
            sankeyNodes.append({"id": "net_savings", "label": surplus_label})
            nodeIndex += 1

        # Create links: Revenue accounts → Income categories
        for revenue_account, categories in revenue_to_category.items():
            for category, amount in categories.items():
                sankeyLinks.append(
                    {
                        "source": revenue_indices[revenue_account],
                        "target": income_category_indices[category],
                        "value": amount,
                    }
                )

        # Create links: Income categories → Income hub
        for income_cat in all_income_categories:
            total_for_cat = sum(
                cats.get(income_cat, 0) for cats in revenue_to_category.values()
            )
            if total_for_cat > 0:
                sankeyLinks.append(
                    {
                        "source": income_category_indices[income_cat],
                        "target": income_hub_index,
                        "value": total_for_cat,
                    }
                )

        # Create links: Income hub → Budgets
        for budget in budgetTotals:
            if float(budget["spent"]) != 0:
                sankeyLinks.append(
                    {
                        "source": income_hub_index,
                        "target": budget_indices[budget["name"]],
                        "value": abs(float(budget["spent"])),
                    }
                )

        # Build actual budget -> category expense mapping using budget transactions
        print("Fetching budget transactions for category mapping...")
        budget_category_map = {}  # budget_name -> {category_name: amount}
        for b in budgetTotals:
            b_id = b["id"]
            # Firefly API: budgets/{id}/transactions with date range
            b_tx_url = (
                f"{config['firefly-url']}/api/v1/budgets/{b_id}/transactions?start="
                + startDate.strftime("%Y-%m-%d")
                + "&end="
                + endDate.strftime("%Y-%m-%d")
            )
            try:
                b_tx_resp = s.get(b_tx_url).json()
            except Exception:
                continue  # skip on error
            for entry in b_tx_resp.get("data", []):
                for t in entry.get("attributes", {}).get("transactions", []):
                    try:
                        amt = float(t.get("amount", 0))
                    except (ValueError, TypeError):
                        amt = 0
                    # We only consider negative amounts as expenses flowing out of the budget
                    if amt >= 0:
                        continue
                    cat_name = t.get("category_name") or "Uncategorized"
                    budget_name = b["name"]
                    if budget_name not in budget_category_map:
                        budget_category_map[budget_name] = {}
                    budget_category_map[budget_name][cat_name] = budget_category_map[
                        budget_name
                    ].get(cat_name, 0) + abs(amt)

        # Create links: Budgets → Categories using real mapped amounts
        for budget_name, cat_map in budget_category_map.items():
            if budget_name not in budget_indices:
                continue
            for cat_name, amt in cat_map.items():
                if cat_name in category_indices and amt > 0:
                    sankeyLinks.append(
                        {
                            "source": budget_indices[budget_name],
                            "target": category_indices[cat_name],
                            "value": amt,
                        }
                    )

        # Add savings flow from income hub
        if savings_index is not None:
            sankeyLinks.append(
                {
                    "source": income_hub_index,
                    "target": savings_index,
                    "value": float(netChangeThisMonth),
                }
            )

        # Convert to JSON for JavaScript
        import json

        sankeyData = json.dumps({"nodes": sankeyNodes, "links": sankeyLinks})
        #
        # Assemble the email
        print("Composing email...")
        msg = EmailMessage()
        msg["Subject"] = config.get("email_subject", "Firefly III: Monthly report")
        msg["From"] = config["email"]["from"]
        msg["To"] = tuple(config["email"]["to"])

        # Build the HTML body with budgets section
        budgetSection = ""
        if budgetsTableBody:
            budgetSection = (
                '<div class="section"><h3>💰 Budget Summary</h3>'
                + budgetsTableBody
                + "</div>"
            )

        htmlBody = """
		<html>
			<head>
				<meta charset="UTF-8">
				<meta name="viewport" content="width=device-width, initial-scale=1.0">
				<style>
					@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=JetBrains+Mono:wght@500&display=swap');
					
					body {{
						font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
						line-height: 1.6;
						color: #1a1a1a;
						max-width: 800px;
						margin: 0 auto;
						padding: 20px;
						background-color: #f5f5f5;
						-webkit-font-smoothing: antialiased;
						-moz-osx-font-smoothing: grayscale;
					}}
					.header {{
						background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
						color: white;
						padding: 30px;
						border-radius: 10px;
						margin-bottom: 30px;
						box-shadow: 0 4px 6px rgba(0,0,0,0.1);
					}}
					.header h1 {{
						margin: 0;
						font-size: 32px;
						font-weight: 700;
						letter-spacing: -0.5px;
					}}
					.header p {{
						margin: 10px 0 0 0;
						opacity: 0.95;
						font-size: 17px;
						font-weight: 400;
						letter-spacing: 0.2px;
					}}
					.section {{
						background: white;
						padding: 25px;
						margin-bottom: 20px;
						border-radius: 8px;
						box-shadow: 0 2px 4px rgba(0,0,0,0.08);
					}}
					h3 {{
						margin: 0 0 20px 0;
						color: #667eea;
						font-size: 22px;
						font-weight: 700;
						border-bottom: 2px solid #f0f0f0;
						padding-bottom: 10px;
						letter-spacing: -0.3px;
					}}
					table {{
						width: 100%;
						border-collapse: collapse;
						margin-top: 10px;
					}}
					th {{
						background-color: #f8f9fa;
						padding: 12px;
						text-align: left;
						font-weight: 600;
						color: #495057;
						border-bottom: 2px solid #dee2e6;
						font-size: 13px;
						text-transform: uppercase;
						letter-spacing: 0.8px;
					}}
					td {{
						padding: 14px 12px;
						border-bottom: 1px solid #f0f0f0;
						font-size: 15px;
					}}
					tr:last-child td {{
						border-bottom: none;
					}}
					tr:hover {{
						background-color: #f8f9fa;
					}}
					.total-row:hover {{
						background-color: #667eea;
					}}
					.amount {{
						font-weight: 600;
						font-family: 'JetBrains Mono', 'SF Mono', 'Monaco', 'Inconsolata', 'Fira Code', 'Droid Sans Mono', 'Courier New', monospace;
						font-size: 15px;
						letter-spacing: -0.3px;
						white-space: nowrap;
					}}
					.positive {{
						color: #28a745;
					}}
					.negative {{
						color: #dc3545;
					}}
					.zero {{
						color: #999;
						font-style: italic;
					}}
					.summary-row {{
						background-color: #f8f9fa;
						font-weight: 600;
					}}
					.total-row {{
						color: white;
						font-weight: 700;
						font-size: 17px;
					}}
					.total-row.positive {{
						background-color: #28a745;
					}}
					.total-row.negative {{
						background-color: #dc3545;
					}}
					.total-row:hover {{
						opacity: 0.95;
					}}
					.total-row td {{
						padding: 16px 12px;
						border-bottom: none;
					}}
					.total-row .amount {{
						color: white !important;
					}}
					canvas {{
						max-width: 100%;
						height: auto !important;
					}}
					.footer {{
						text-align: center;
						margin-top: 30px;
						padding: 20px;
						color: #999;
						font-size: 13px;
						font-weight: 400;
					}}
					
					/* Mobile responsive styles */
					@media only screen and (max-width: 600px) {{
						body {{
							padding: 10px;
						}}
						.header {{
							padding: 20px;
						}}
						.header h1 {{
							font-size: 24px;
						}}
						.section {{
							padding: 15px;
						}}
						h3 {{
							font-size: 18px;
						}}
						th, td {{
							padding: 10px 8px;
							font-size: 14px;
						}}
						.amount {{
							font-size: 14px;
						}}
						th {{
							font-size: 11px;
						}}
					}}
				</style>
			</head>
			<body>
				<div class="header">
					<h1>📊 Firefly III Monthly Report</h1>
					<p>{monthName} {year}</p>
				</div>
				<div class="section">
					<h3>🏷️ Category Summary</h3>
					{categoriesTableBody}
				</div>
				<div class="section">
					<h3>💸 Money Flow</h3>
					<div style="position: relative; height: 500px;">
						<canvas id="sankeyChart"></canvas>
					</div>
				</div>
				{budgetSection}
				<div class="section">
					<h3>📈 Financial Overview</h3>
					{generalTableBody}
				</div>
				<div class="footer">
					Generated by <a href="https://github.com/yemzikk/firefly-iii-email-summary" style="color: #999;">Firefly III Email Summary</a> • <a href="https://yemzikk.in" style="color: #999; text-decoration: none;">Yemzikk</a>
				</div>
				<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.js"></script>
				<script src="https://cdn.jsdelivr.net/npm/chartjs-chart-sankey@0.12.0/dist/chartjs-chart-sankey.min.js"></script>
				<script>
					const sankeyData = {sankeyData};
					const ctx = document.getElementById('sankeyChart');
					
					// Transform data for Chart.js Sankey
					const data = {{
						datasets: [{{
							data: sankeyData.links.map(link => ({{
								from: sankeyData.nodes[link.source].label,
								to: sankeyData.nodes[link.target].label,
								flow: link.value
							}})),
							colorFrom: (c) => {{
								const fromLabel = c.dataset.data[c.dataIndex].from;
								const toLabel = c.dataset.data[c.dataIndex].to;
								// Revenue accounts (Level 1 - dark green)
								if (toLabel !== 'Total Income' && fromLabel !== 'Total Income') return 'rgba(27, 94, 32, 0.7)';
								// Income categories (Level 2 - light green)
								if (toLabel === 'Total Income') return 'rgba(76, 175, 80, 0.7)';
								// Income hub (Level 3 - blue)
								if (fromLabel === 'Total Income') return 'rgba(102, 126, 234, 0.7)';
								// Budgets (Level 4 - purple)
								return 'rgba(156, 39, 176, 0.7)';
							}},
							colorTo: (c) => {{
								const toLabel = c.dataset.data[c.dataIndex].to;
								const fromLabel = c.dataset.data[c.dataIndex].from;
								// Surplus Savings (green)
								if (toLabel === 'Savings' || toLabel === 'Net Savings') return 'rgba(40, 167, 69, 0.7)';
								// Income categories (light green)
								if (toLabel !== 'Total Income' && fromLabel !== 'Total Income') return 'rgba(76, 175, 80, 0.7)';
								// Income hub (blue)
								if (toLabel === 'Total Income') return 'rgba(102, 126, 234, 0.7)';
								// Budgets (purple)
								if (fromLabel === 'Total Income' && toLabel !== 'Savings' && toLabel !== 'Net Savings') return 'rgba(156, 39, 176, 0.7)';
								// Categories (red)
								return 'rgba(220, 53, 69, 0.7)';
							}},
							borderWidth: 0,
							nodeWidth: 10,
							size: 'max'
						}}]
					}};
					
					new Chart(ctx, {{
						type: 'sankey',
						data: data,
						options: {{
							responsive: true,
							maintainAspectRatio: false,
							plugins: {{
								legend: {{
									display: false
								}},
								tooltip: {{
									callbacks: {{
										label: function(context) {{
											const item = context.dataset.data[context.dataIndex];
											const totalIncome = {totalIncome};
											const percentage = ((item.flow / totalIncome) * 100).toFixed(1);
											return item.from + ' → ' + item.to + ': {currencySymbol}' + Math.round(item.flow).toLocaleString() + ' (' + percentage + '%)';
										}}
									}}
								}}
							}}
						}}
					}});
				</script>
			</body>
		</html>
		""".format(
            monthName=monthName,
            year=startDate.strftime("%Y"),
            categoriesTableBody=categoriesTableBody,
            budgetSection=budgetSection,
            generalTableBody=generalTableBody,
            sankeyData=sankeyData,
            currencySymbol=currencySymbol,
            totalIncome=earnedThisMonth,
        )
        msg.set_content(
            bs4.BeautifulSoup(htmlBody, "html.parser").get_text()
        )  # just html to text
        msg.add_alternative(htmlBody, subtype="html")
        #
        # Check if we're in preview mode
        if args.preview:
            # Generate preview.html file
            preview_path = os.path.join(base_dir, "preview.html")
            # Create a standalone HTML document
            preview_html = """<!DOCTYPE html>
<html>
	<head>
		<meta charset="UTF-8">
		<meta name="viewport" content="width=device-width, initial-scale=1.0">
		<title>Firefly III Monthly Report - Preview</title>
	</head>
	{body}
</html>""".format(
                body=htmlBody
            )

            with open(preview_path, "w", encoding="utf-8") as f:
                f.write(preview_html)

            print(f"✅ Preview generated: {preview_path}")
            print(f"   Open in browser: file://{preview_path}")
            return
        #
        # Set up the SSL context for SMTP if necessary
        context = ssl.create_default_context()
        #
        # Send off the message
        print("Sending email...")
        try:
            with smtplib.SMTP(
                host=config["smtp"]["server"], port=config["smtp"]["port"]
            ) as s:
                s.set_debuglevel(0)  # Set to 1 for debugging
                if config["smtp"]["starttls"]:
                    s.ehlo()
                    try:
                        s.starttls(context=context)
                        s.ehlo()  # Re-identify after STARTTLS
                    except Exception as e:
                        traceback.print_exc()
                        print(
                            f"ERROR: could not connect to SMTP server with STARTTLS: {e}"
                        )
                        sys.exit(2)
                if config["smtp"]["authentication"]:
                    try:
                        s.login(
                            user=config["smtp"]["user"],
                            password=config["smtp"]["password"],
                        )
                    except Exception as e:
                        traceback.print_exc()
                        print(f"ERROR: could not authenticate with SMTP server: {e}")
                        sys.exit(3)
                s.send_message(msg)
                print("✅ Email sent successfully!")

            # Optional: Ping healthcheck URL if configured
            if "healthcheck_url" in config and config["healthcheck_url"]:
                print("Pinging healthcheck...")
                try:
                    ping_response = requests.get(config["healthcheck_url"], timeout=10)
                    if ping_response.status_code == 200:
                        print("✅ Healthcheck ping sent successfully!")
                    else:
                        print(
                            f"⚠️  Healthcheck ping returned status code: {ping_response.status_code}"
                        )
                except Exception as e:
                    print(f"⚠️  Warning: Could not send healthcheck ping: {e}")
                    # Don't exit on healthcheck failure - email was sent successfully

        except Exception as e:
            traceback.print_exc()
            print(f"ERROR: Failed to send email: {e}")
            sys.exit(4)


if __name__ == "__main__":
    main()
