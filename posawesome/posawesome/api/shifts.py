# -*- coding: utf-8 -*-
# Copyright (c) 2020, Youssef Restom and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import json
import frappe
from frappe.utils import nowdate, flt
from frappe import _
from .utilities import get_version


@frappe.whitelist()
def get_opening_dialog_data():
    data = {}

    # Get only POS Profiles where current user is defined in POS Profile User table
    pos_profiles_data = frappe.db.sql(
        """
        SELECT DISTINCT p.name, p.company, p.currency 
        FROM `tabPOS Profile` p
        INNER JOIN `tabPOS Profile User` u ON u.parent = p.name
        WHERE p.disabled = 0 AND u.user = %s
        ORDER BY p.name
    """,
        frappe.session.user,
        as_dict=1,
    )

    data["pos_profiles_data"] = pos_profiles_data

    # Derive companies from accessible POS Profiles
    company_names = []
    for profile in pos_profiles_data:
        if profile.company and profile.company not in company_names:
            company_names.append(profile.company)
    data["companies"] = [{"name": c} for c in company_names]

    pos_profiles_list = []
    for i in data["pos_profiles_data"]:
        pos_profiles_list.append(i.name)

    payment_method_table = (
        "POS Payment Method" if get_version() == 13 else "Sales Invoice Payment"
    )
    data["payments_method"] = frappe.get_list(
        payment_method_table,
        filters={"parent": ["in", pos_profiles_list]},
        fields=["*"],
        limit_page_length=0,
        order_by="parent",
        ignore_permissions=True,
    )
    # set currency from pos profile
    for mode in data["payments_method"]:
        mode["currency"] = frappe.get_cached_value(
            "POS Profile", mode["parent"], "currency"
        )

    return data


@frappe.whitelist()
def get_todays_shifts_summary():
    """Get summary of all opening shifts that happened today"""
    from frappe.utils import today, getdate
    
    # Get all opening shifts that are active today (started today OR started yesterday but still open)
    opening_shifts = frappe.get_all(
        "POS Opening Shift",
        filters={
            "docstatus": 1,
            "status": "Open"
        },
        fields=[
            "name", "user", "pos_profile", "company", 
            "period_start_date", "period_end_date", "status"
        ],
        order_by="period_start_date desc"
    )
    
    # Filter to only include shifts that have transactions today
    # or shifts that started today or are still active from yesterday
    from frappe.utils import getdate, add_days
    today_date = getdate(today())
    yesterday_date = add_days(today_date, -1)
    
    filtered_shifts = []
    for shift in opening_shifts:
        shift_start_date = getdate(shift.period_start_date)
        
        # Include shift if:
        # 1. It started today, OR
        # 2. It started yesterday and is still open (overnight shift)
        if shift_start_date == today_date or (shift_start_date == yesterday_date and shift.status == "Open"):
            filtered_shifts.append(shift)
    
    opening_shifts = filtered_shifts
    
    # Get company name and currency from the first shift or default
    company_name = "Company"
    company_currency = "QAR"  # Default fallback
    if opening_shifts:
        company_name = frappe.get_cached_value("Company", opening_shifts[0].company, "company_name") or opening_shifts[0].company
        company_currency = frappe.get_cached_value("Company", opening_shifts[0].company, "default_currency") or "QAR"
    
    summary_data = {
        "date": today(),
        "total_shifts": len(opening_shifts),
        "company_name": company_name,
        "company_currency": company_currency,
        "shifts": [],
        "overall_totals": {
            "grand_total": 0,
            "net_total": 0,
            "total_quantity": 0,
            "total_transactions": 0
        },
        "overall_items_summary": {},
        "overall_customers_summary": {}
    }
    
    # Get payment methods for all POS profiles
    pos_profiles = list(set([shift.pos_profile for shift in opening_shifts]))
    payment_methods = {}
    for profile in pos_profiles:
        payment_methods[profile] = frappe.get_all(
            "POS Payment Method" if get_version() == 13 else "Sales Invoice Payment",
            filters={"parent": profile},
            fields=["mode_of_payment"],
            pluck="mode_of_payment"
        )
    
    # Get currency for each POS profile
    currencies = {}
    for profile in pos_profiles:
        currencies[profile] = frappe.get_cached_value("POS Profile", profile, "currency")
    
    # Process each shift
    for shift in opening_shifts:
        shift_data = {
            "shift_name": shift.name,
            "user": shift.user,
            "pos_profile": shift.pos_profile,
            "company": shift.company,
            "start_time": shift.period_start_date,
            "end_time": shift.period_end_date,
            "status": shift.status,
            "currency": currencies.get(shift.pos_profile, company_currency),
            "totals": {
                "grand_total": 0,
                "net_total": 0,
                "total_quantity": 0,
                "total_transactions": 0
            },
            "payment_summary": {},
            "taxes": [],
            "items_summary": {},  # Add items summary
            "customers_summary": {}  # Add customers summary
        }
        
        # Get invoices for this shift
        use_pos_invoice = frappe.db.get_value(
            "POS Profile", shift.pos_profile, "create_pos_invoice_instead_of_sales_invoice"
        )
        doctype = "POS Invoice" if use_pos_invoice else "Sales Invoice"
        
        invoices = frappe.get_all(
            doctype,
            filters={
                "posa_pos_opening_shift": shift.name,
                "docstatus": 1
            },
            fields=[
                "name", "grand_total", "net_total", "total_qty", 
                "posting_date", "customer"
            ]
        )
        
        # Process each invoice to get totals, payments, taxes, and items
        shift_data["invoices"] = []
        
        for invoice in invoices:
            # Calculate totals from basic invoice data
            shift_data["totals"]["grand_total"] += flt(invoice.grand_total)
            shift_data["totals"]["net_total"] += flt(invoice.net_total)
            shift_data["totals"]["total_quantity"] += flt(invoice.total_qty)
            shift_data["totals"]["total_transactions"] += 1
            
            # Load full invoice document to get payments, taxes, and items
            invoice_doc = frappe.get_doc(doctype, invoice.name)
            
            # Create invoice summary with items
            invoice_summary = {
                "name": invoice.name,
                "customer": invoice.customer,
                "posting_date": invoice.posting_date,
                "grand_total": flt(invoice.grand_total),
                "net_total": flt(invoice.net_total),
                "total_qty": flt(invoice.total_qty),
                "invoice_items": [],
                "payments": [],
                "taxes": []
            }
            
            # Get items
            for item in invoice_doc.items:
                item_data = {
                    "item_code": item.item_code,
                    "item_name": item.item_name,
                    "qty": flt(item.qty),
                    "rate": flt(item.rate),
                    "amount": flt(item.amount)
                }
                invoice_summary["invoice_items"].append(item_data)
                
                # Add to items summary
                item_key = f"{item.item_code} - {item.item_name}"
                if item_key in shift_data["items_summary"]:
                    shift_data["items_summary"][item_key]["qty"] += flt(item.qty)
                    shift_data["items_summary"][item_key]["amount"] += flt(item.amount)
                else:
                    shift_data["items_summary"][item_key] = {
                        "item_code": item.item_code,
                        "item_name": item.item_name,
                        "qty": flt(item.qty),
                        "rate": flt(item.rate),
                        "amount": flt(item.amount)
                    }
            
            # Get payments
            for p in invoice_doc.payments:
                invoice_summary["payments"].append({
                    "mode_of_payment": p.mode_of_payment,
                    "amount": flt(p.amount)
                })
                
                # Also add to shift payment summary
                if p.mode_of_payment in shift_data["payment_summary"]:
                    shift_data["payment_summary"][p.mode_of_payment] += flt(p.amount)
                else:
                    shift_data["payment_summary"][p.mode_of_payment] = flt(p.amount)
            
            # Get taxes
            for t in invoice_doc.taxes:
                invoice_summary["taxes"].append({
                    "account_head": t.account_head,
                    "rate": t.rate,
                    "tax_amount": flt(t.tax_amount)
                })
                
                # Also add to shift tax summary
                existing_tax = next((tax for tax in shift_data["taxes"] 
                                   if tax["account_head"] == t.account_head and tax["rate"] == t.rate), None)
                if existing_tax:
                    existing_tax["amount"] += flt(t.tax_amount)
                else:
                    shift_data["taxes"].append({
                        "account_head": t.account_head,
                        "rate": t.rate,
                        "amount": flt(t.tax_amount)
                    })
            
            shift_data["invoices"].append(invoice_summary)
            
            customer = invoice.customer
            if customer: 
                # Check if customer group is Commercial or Individual
                customer_group = frappe.get_cached_value("Customer", customer, "customer_group")
                if customer_group in ["Commercial", "Individual"]:
                    if customer in shift_data["customers_summary"]:
                        shift_data["customers_summary"][customer]["grand_total"] += flt(invoice.grand_total)
                        shift_data["customers_summary"][customer]["net_total"] += flt(invoice.net_total)
                        shift_data["customers_summary"][customer]["total_qty"] += flt(invoice.total_qty)
                        shift_data["customers_summary"][customer]["transactions"] += 1
                        if "customer_group" not in shift_data["customers_summary"][customer]:
                            shift_data["customers_summary"][customer]["customer_group"] = customer_group
                        
                        shift_data["customers_summary"][customer]["transactions_list"].append({
                            "invoice_name": invoice.name,
                            "posting_date": invoice.posting_date,
                            "grand_total": flt(invoice.grand_total),
                            "net_total": flt(invoice.net_total),
                            "total_qty": flt(invoice.total_qty),
                            "items": invoice_summary["invoice_items"]
                        })
                        
                        for item in invoice_summary["invoice_items"]:
                            item_key = f"{item['item_code']} - {item['item_name']}"
                            if item_key in shift_data["customers_summary"][customer]["items_summary"]:
                                shift_data["customers_summary"][customer]["items_summary"][item_key]["qty"] += flt(item["qty"])
                                shift_data["customers_summary"][customer]["items_summary"][item_key]["amount"] += flt(item["amount"])
                            else:
                                shift_data["customers_summary"][customer]["items_summary"][item_key] = {
                                    "item_code": item["item_code"],
                                    "item_name": item["item_name"],
                                    "qty": flt(item["qty"]),
                                    "rate": flt(item["rate"]),
                                    "amount": flt(item["amount"])
                                }
                    else:
                        shift_data["customers_summary"][customer] = {
                            "customer": customer,
                            "customer_group": customer_group,
                            "grand_total": flt(invoice.grand_total),
                            "net_total": flt(invoice.net_total),
                            "total_qty": flt(invoice.total_qty),
                            "transactions": 1,
                            "transactions_list": [{
                                "invoice_name": invoice.name,
                                "posting_date": invoice.posting_date,
                                "grand_total": flt(invoice.grand_total),
                                "net_total": flt(invoice.net_total),
                                "total_qty": flt(invoice.total_qty),
                                "items": invoice_summary["invoice_items"]
                            }],
                            "items_summary": {}
                        }
                        
                        for item in invoice_summary["invoice_items"]:
                            item_key = f"{item['item_code']} - {item['item_name']}"
                            shift_data["customers_summary"][customer]["items_summary"][item_key] = {
                                "item_code": item["item_code"],
                                "item_name": item["item_name"],
                                "qty": flt(item["qty"]),
                                "rate": flt(item["rate"]),
                                "amount": flt(item["amount"])
                            }
        
        shift_data["items_summary"] = list(shift_data["items_summary"].values())
        
        for customer in shift_data["customers_summary"].values():
            customer["items_summary"] = list(customer["items_summary"].values())
        shift_data["customers_summary"] = list(shift_data["customers_summary"].values())
        
        # Add to overall totals
        summary_data["overall_totals"]["grand_total"] += shift_data["totals"]["grand_total"]
        summary_data["overall_totals"]["net_total"] += shift_data["totals"]["net_total"]
        summary_data["overall_totals"]["total_quantity"] += shift_data["totals"]["total_quantity"]
        summary_data["overall_totals"]["total_transactions"] += shift_data["totals"]["total_transactions"]
        
        # Add to overall items summary
        for item in shift_data["items_summary"]:
            item_key = f"{item['item_code']} - {item['item_name']}"
            if item_key in summary_data["overall_items_summary"]:
                summary_data["overall_items_summary"][item_key]["qty"] += flt(item["qty"])
                summary_data["overall_items_summary"][item_key]["amount"] += flt(item["amount"])
            else:
                summary_data["overall_items_summary"][item_key] = {
                    "item_code": item["item_code"],
                    "item_name": item["item_name"],
                    "qty": flt(item["qty"]),
                    "rate": flt(item["rate"]),
                    "amount": flt(item["amount"])
                }
        
        for customer in shift_data["customers_summary"]:
            customer_key = customer["customer"]
            if customer_key in summary_data["overall_customers_summary"]:
                summary_data["overall_customers_summary"][customer_key]["grand_total"] += flt(customer["grand_total"])
                summary_data["overall_customers_summary"][customer_key]["net_total"] += flt(customer["net_total"])
                summary_data["overall_customers_summary"][customer_key]["total_qty"] += flt(customer["total_qty"])
                summary_data["overall_customers_summary"][customer_key]["transactions"] += customer["transactions"]
                
                for transaction in customer.get("transactions_list", []):
                    summary_data["overall_customers_summary"][customer_key]["transactions_list"].append(transaction)
                
                for item in customer.get("items_summary", []):
                    item_key = f"{item['item_code']} - {item['item_name']}"
                    if item_key in summary_data["overall_customers_summary"][customer_key]["items_summary"]:
                        summary_data["overall_customers_summary"][customer_key]["items_summary"][item_key]["qty"] += flt(item["qty"])
                        summary_data["overall_customers_summary"][customer_key]["items_summary"][item_key]["amount"] += flt(item["amount"])
                    else:
                        summary_data["overall_customers_summary"][customer_key]["items_summary"][item_key] = {
                            "item_code": item["item_code"],
                            "item_name": item["item_name"],
                            "qty": flt(item["qty"]),
                            "rate": flt(item["rate"]),
                            "amount": flt(item["amount"])
                        }
            else:
                summary_data["overall_customers_summary"][customer_key] = {
                    "customer": customer["customer"],
                    "customer_group": customer.get("customer_group", ""),
                    "grand_total": flt(customer["grand_total"]),
                    "net_total": flt(customer["net_total"]),
                    "total_qty": flt(customer["total_qty"]),
                    "transactions": customer["transactions"],
                    "transactions_list": customer.get("transactions_list", []).copy(),
                    "items_summary": {}
                }
                
                for item in customer.get("items_summary", []):
                    item_key = f"{item['item_code']} - {item['item_name']}"
                    summary_data["overall_customers_summary"][customer_key]["items_summary"][item_key] = {
                        "item_code": item["item_code"],
                        "item_name": item["item_name"],
                        "qty": flt(item["qty"]),
                        "rate": flt(item["rate"]),
                        "amount": flt(item["amount"])
                    }
        
        summary_data["shifts"].append(shift_data)
    
    summary_data["overall_items_summary"] = list(summary_data["overall_items_summary"].values())
    
    for customer in summary_data["overall_customers_summary"].values():
        customer["items_summary"] = list(customer["items_summary"].values())
    summary_data["overall_customers_summary"] = list(summary_data["overall_customers_summary"].values())
    
    return summary_data


@frappe.whitelist()
def create_opening_voucher(pos_profile, company, balance_details):
    balance_details = json.loads(balance_details)

    new_pos_opening = frappe.get_doc(
        {
            "doctype": "POS Opening Shift",
            "period_start_date": frappe.utils.get_datetime(),
            "posting_date": frappe.utils.getdate(),
            "user": frappe.session.user,
            "pos_profile": pos_profile,
            "company": company,
            "docstatus": 1,
        }
    )
    new_pos_opening.set("balance_details", balance_details)
    new_pos_opening.insert(ignore_permissions=True)

    data = {}
    data["pos_opening_shift"] = new_pos_opening.as_dict()
    update_opening_shift_data(data, new_pos_opening.pos_profile)
    return data


@frappe.whitelist()
def check_opening_shift(user):
    open_vouchers = frappe.db.get_all(
        "POS Opening Shift",
        filters={
            "user": user,
            "pos_closing_shift": ["in", ["", None]],
            "docstatus": 1,
            "status": "Open",
        },
        fields=["name", "pos_profile"],
        order_by="period_start_date desc",
    )
    data = ""
    if len(open_vouchers) > 0:
        data = {}
        data["pos_opening_shift"] = frappe.get_doc(
            "POS Opening Shift", open_vouchers[0]["name"]
        )
        update_opening_shift_data(data, open_vouchers[0]["pos_profile"])
    return data


def update_opening_shift_data(data, pos_profile):
    data["pos_profile"] = frappe.get_doc("POS Profile", pos_profile)
    if data["pos_profile"].get("posa_language"):
        frappe.local.lang = data["pos_profile"].posa_language
    data["company"] = frappe.get_doc("Company", data["pos_profile"].company)
    allow_negative_stock = frappe.get_value(
        "Stock Settings", None, "allow_negative_stock"
    )
    data["stock_settings"] = {}
    data["stock_settings"].update({"allow_negative_stock": allow_negative_stock})
