# -*- coding: utf-8 -*-
# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cstr, flt, nowdate, nowtime, cint
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note, make_sales_invoice
from erpnext_shopify.utils import get_request, get_shopify_customers, get_address_type, post_request,\
 get_shopify_items, get_shopify_orders, get_shopify_customer_by_id, get_collection_by_product_id

import datetime, uuid, copy, re

from datetime import timedelta

shopify_variants_attr_list = ["option1", "option2", "option3"] 

class ShopifyError(Exception):pass

class ShopifySettings(Document): pass
    
@frappe.whitelist()
def get_series():
    return {
        "sales_order_series" : frappe.get_meta("Sales Order").get_options("naming_series") or "SO-Shopify-",
        "sales_invoice_series" : frappe.get_meta("Sales Invoice").get_options("naming_series")  or "SI-Shopify-",
        "delivery_note_series" : frappe.get_meta("Delivery Note").get_options("naming_series")  or "DN-Shopify-"
    }

@frappe.whitelist() 
def sync_shopify():
    shopify_settings = frappe.get_doc("Shopify Settings", "Shopify Settings")
    
    if not frappe.session.user:
        user = frappe.db.sql("""select parent from tabUserRole 
            where role = "System Manager" and parent not in ('administrator', "Administrator") limit 1""", as_list=1)[0][0]
        frappe.set_user(user)
        
    if shopify_settings.enable_shopify:
        try :
            # sync_customers()
            # sync_products(shopify_settings.price_list, shopify_settings.warehouse)
            sync_orders()
            
        except ShopifyError:
            raise ValueError(ShopifyError)

def sync_products(price_list, warehouse):
    sync_shopify_items(warehouse)

def sync_shopify_items(warehouse):
    shopify_items = get_shopify_items()

    # 262
    for item in shopify_items:
        make_item(warehouse, item)

def make_item(warehouse, item):
    existing_erp_item = frappe.db.sql("""select item_code, item_name, item_group, description from tabItem where shopify_id = %(shopify_id)s""", {"shopify_id": item.get("id")}, as_dict = 1)
    actual_item_group = get_item_group(item.get("id"), item.get("product_type"))
    if existing_erp_item:
        #
        ## For now, we support "item_name", "description", "item_group", "attributes", "price" update
        #
        # Deal with "item_name", "description", "item_group" update
        frappe.db.set_value("Item", existing_erp_item[0]["item_code"], "item_name", item.get("title"))
        frappe.db.set_value("Item", existing_erp_item[0]["item_code"], "description", item.get("title") or u"Please refer to the product pics.")
        frappe.db.set_value("Item", existing_erp_item[0]["item_code"], "item_group", actual_item_group)

        # Deal with "attributes(variants)" update
        if has_variants(item):
            attributes = create_attribute(item)
            create_item_variants(item, warehouse, attributes, shopify_variants_attr_list, actual_item_group, existing_erp_item[0]["item_code"])
    else:
        # Need to proceed the creation at this point
        if has_variants(item):
            attributes = create_attribute(item)
            create_item(item, warehouse, actual_item_group, 1, attributes)
            create_item_variants(item, warehouse, attributes, shopify_variants_attr_list, actual_item_group)
        else:
            create_item(item, warehouse, actual_item_group)
                
def has_variants(item):
    if len(item.get("options")) > 0 and "Default Title" not in item.get("options")[0]["values"]:
        return True
    return False
    
def create_attribute(item):
    attribute = []
    for attr in item.get('options'):
        if not frappe.db.get_value("Item Attribute", attr.get("name"), "name"):
            frappe.get_doc({
                "doctype": "Item Attribute",
                "attribute_name": attr.get("name"),
                "item_attribute_values": [{"attribute_value":attr_value, "abbr": cstr(attr_value)} for attr_value in attr.get("values")]
            }).insert()
            
        else:
            "check for attribute values"
            item_attr = frappe.get_doc("Item Attribute", attr.get("name"))
            set_new_attribute_values(item_attr, attr.get("values"))
            item_attr.save()
        
        attribute.append({"attribute": attr.get("name")})
    return attribute
    
def set_new_attribute_values(item_attr, values):
    for attr_value in values:
        if not any((d.abbr == attr_value or d.attribute_value == attr_value) for d in item_attr.item_attribute_values):
            item_attr.append("item_attribute_values", {
                "attribute_value": attr_value,
                "abbr": cstr(attr_value)
            })

def get_attributes_string(attributes):
    temp = ""
    for attr_item in attributes:
        temp += attr_item["attribute_value"]
        temp += u"-"
    return temp[:-1]
    
def create_item(item, warehouse, actual_item_group, has_variant=0, attributes=[], variant_of=None):
    temp_item_name_with_attributes = item.get("title") + u"--" + get_attributes_string(attributes) if variant_of else item.get("title")

    item_name = frappe.get_doc({
        "doctype": "Item",
        "shopify_id": item.get("id"),
        "variant_of": variant_of,
        "item_code": cstr(item.get("item_code")) or cstr(item.get("id")),
        "item_name": temp_item_name_with_attributes,
        "description": item.get("title") or u"Please refer to the product pics.",
        "item_group": actual_item_group,
        "has_variants": has_variant,
        "attributes": attributes,
        "stock_uom": item.get("uom") or get_stock_uom(item), 
        "default_warehouse": warehouse
    }).insert()
    if not has_variant:
        add_to_price_list(item)

def create_item_variants(item, warehouse, attributes, shopify_variants_attr_list, actual_item_group, existing_erp_item_code = None):
    for variant in item.get("variants"):
        variant_item = {
            "id" : variant.get("id"),
            "item_code": variant.get("id"),
            "title": item.get("title"),
            "product_type": actual_item_group,
            "uom": get_stock_uom(item),
            "item_price": variant.get("price")
        }
        
        for i, variant_attr in enumerate(shopify_variants_attr_list):
            if variant.get(variant_attr):
                attributes[i].update({"attribute_value": get_attribute_value(variant.get(variant_attr), attributes[i])})

        if existing_erp_item_code:
            temp_attributes_copy = copy.deepcopy(attributes)
            original_variants = frappe.db.sql("""select attribute, attribute_value from `tabItem Variant Attribute` where parent in (select item_code from `tabItem` where variant_of = %(item_code)s) group by attribute, attribute_value""", {"item_code": existing_erp_item_code}, as_dict = 1)

            # Find out which variant need to be created
            temp = len(temp_attributes_copy) - 1
            while temp >= 0:
                for original_variant_item in original_variants:
                    if temp_attributes_copy[temp]["attribute"] == original_variant_item.get("attribute") and temp_attributes_copy[temp]["attribute_value"] == original_variant_item.get("attribute_value"):
                        del temp_attributes_copy[temp]
                        break
                temp = temp - 1

            if len(temp_attributes_copy):
                create_item(variant_item, warehouse, actual_item_group, 0, temp_attributes_copy, cstr(item.get("id")))

            continue

        create_item(variant_item, warehouse, actual_item_group, 0, attributes, cstr(item.get("id")))
        
def get_attribute_value(variant_attr_val, attribute):
    return frappe.db.sql("""select attribute_value from `tabItem Attribute Value` 
        where parent = '{0}' and (abbr = '{1}' or attribute_value = '{2}')""".format(attribute["attribute"], variant_attr_val, variant_attr_val))[0][0]

def get_item_group(product_id, product_type=None):
    actual_item_group = None

    collections = get_collection_by_product_id(product_id)
    
    actual_item_group = collections[0]["title"] if collections else product_type

    if actual_item_group:
        if not frappe.db.get_value("Item Group", actual_item_group, "name"):
            return frappe.get_doc({
                "doctype": "Item Group",
                "item_group_name": actual_item_group,
                "parent_item_group": _("All Item Groups"),
                "is_group": "No"
            }).insert().name
        else:
            return actual_item_group
    else:
        return _("All Item Groups")

def get_stock_uom(item):
    sku = item.get("variants")[0].get("sku")
    if sku:
        if not frappe.db.get_value("UOM", sku, "name"):
            return frappe.get_doc({
                "doctype": "UOM",
                "uom_name": item.get("variants")[0].get("sku")
            }).insert().name
        else:
            return sku
    else:
        return _("Nos")

def add_to_price_list(item):
    frappe.get_doc({
        "doctype": "Item Price",
        "price_list": frappe.get_doc("Shopify Settings", "Shopify Settings").price_list,
        "item_code": cstr(item.get("item_code")) or cstr(item.get("id")),
        "price_list_rate": item.get("item_price") or item.get("variants")[0].get("price")
    }).insert()

def get_price_and_stock_details(item, uom, warehouse, price_list):
    qty = frappe.db.get_value("Bin", {"item_code":item.get("item_code"), "warehouse": warehouse}, "actual_qty") 
    price = frappe.db.get_value("Item Price", \
            {"price_list": price_list, "item_code":item.get("item_code")}, "price_list_rate")
            
    item_price_and_quantity = {
        "price": flt(price), 
        "sku": uom,
        "inventory_quantity": cint(qty) if qty else 0,
        "inventory_management": "shopify"
    }
    
    return item_price_and_quantity
    
def sync_customers():
    sync_shopify_customers()

def sync_shopify_customers():
    for customer in get_shopify_customers():
        create_customer(customer)

def create_customer(customer):
    erp_cust = None

    erp_customer = frappe.db.sql("""select name, customer_name from tabCustomer where shopify_id = %(shopify_id)s""", {"shopify_id": customer.get("id")}, as_dict = 1)
    
    if erp_customer:
        if not customer.get("first_name").strip().startswith('00000'):
            # Proceed the customer update here
            frappe.db.set_value("Customer", erp_customer[0]["name"], "customer_name", customer.get("first_name"))
        frappe.db.set_value("Customer", erp_customer[0]["name"], "full_name", customer.get("last_name") or u"")
    else:
        cust_name = customer.get("first_name") if not customer.get("first_name").strip().startswith('00000') else customer.get("first_name").strip() + u'-' + str(uuid.uuid4())
        try:
            erp_cust = frappe.get_doc({
                "doctype": "Customer",
                "name": customer.get("id"),
                "customer_name": cust_name,
                "full_name": customer.get("last_name") or u"",
                "shopify_id": customer.get("id"),
                "customer_group": "Individual",
                "territory": "All Territories",
                "customer_type": "Company"
            }).insert()
        except:
            pass

def sync_orders():
    sync_shopify_orders()

def sync_shopify_orders():
    orders = filter(lambda x: datetime.datetime.strptime(x["processed_at"][:-6], "%Y-%m-%dT%H:%M:%S") > datetime.datetime.strptime('2015-11-17T00:00:00' ,'%Y-%m-%dT%H:%M:%S'), get_shopify_orders())

    orders = sorted(orders, key=lambda x: datetime.datetime.strptime(x["processed_at"][:-6], "%Y-%m-%dT%H:%M:%S"))

    # 577
    for order in orders[0:20]:
        if not order.get("customer"):
            order["customer"] = {}
            order["customer"]["total_spent"] = order["subtotal_price"]
            order["customer"]["first_name"] = u"-00243"
            order["customer"]["last_name"] = u"Non Member"
            order["customer"]["last_order_name"] = u"#3-1473"
            order["customer"]["orders_count"] = 1
            order["customer"]["created_at"] = u"2015-11-06T15:20:53+08:00"
            order["customer"]["tags"] = u""
            order["customer"]["updated_at"] = u"2015-11-07T19:43:20+08:00"
            order["customer"]["email"] = None
            order["customer"]["note"] = u""

            order["customer"]["default_address"] = {}
            order["customer"]["default_address"]["province"] = u"Pulau Pinang"
            order["customer"]["default_address"]["city"] = u""
            order["customer"]["default_address"]["first_name"] = u"Non"
            order["customer"]["default_address"]["last_name"] = u"Member"
            order["customer"]["default_address"]["name"] = u"Non Member"
            order["customer"]["default_address"]["zip"] = u"10300"
            order["customer"]["default_address"]["province_code"] = u"PNG"
            order["customer"]["default_address"]["default"] = True
            order["customer"]["default_address"]["address1"] = u""
            order["customer"]["default_address"]["address2"] = u""
            order["customer"]["default_address"]["id"] = 1988439940
            order["customer"]["default_address"]["phone"] = u""
            order["customer"]["default_address"]["country_code"] = u"MY"
            order["customer"]["default_address"]["country"] = u"Malaysia"
            order["customer"]["default_address"]["country_name"] = u"Malaysia"
            order["customer"]["default_address"]["company"] = u""

            order["customer"]["state"] = u"disabled"
            order["customer"]["multipass_identifier"] = None
            order["customer"]["tax_exempt"] = False
            order["customer"]["accepts_marketing"] = False
            order["customer"]["id"] = 1828210884
            order["customer"]["last_order_id"] = 1777711300
            order["customer"]["verified_email"] = False

        validate_customer_and_product(order)
        create_order(order)

def validate_customer_and_product(order):
    create_customer(get_shopify_customer_by_id(order.get("customer").get("id")))
    
    warehouse = frappe.get_doc("Shopify Settings", "Shopify Settings").warehouse

    for item in order.get("line_items"):
        item = get_request("/admin/products/{}.json".format(item.get("product_id")))["product"]
        make_item(warehouse, item)

def create_employee(employee_id, employee_name):
    if not frappe.db.sql("""select employee_id from tabEmployee where employee_id = %(employee_id)s""", {"employee_id": employee_id}, as_dict = 1):
        try:
            employee = frappe.get_doc({
                "doctype": "Employee",
                "employee_name": employee_name,
                "employee_id": employee_id
            }).insert()
        except Exception, e:
            pass

def create_order(order):
    shopify_settings = frappe.get_doc("Shopify Settings", "Shopify Settings")
    so = create_sales_order(order, shopify_settings)
    if so:
        if order.get("financial_status") == "paid":
            create_sales_invoice(order, shopify_settings, so)
            
        if order.get("fulfillments"):
            create_delivery_note(order, shopify_settings, so)

def create_sales_order(order, shopify_settings):

    shopify_employee_name = None

    # Deal with 'user_id in order entry' and 'employee accounts' mapping
    if order.get("user_id") == 26626308:
        shopify_employee_name = u"Joyce Teoh"
    elif order.get("user_id") == 26626372:
        shopify_employee_name = u"Lucus Tan"
    elif order.get("user_id") == 29492868:
        shopify_employee_name = u"Vong Guat Theng"
    elif order.get("user_id") == 29527236:
        shopify_employee_name = u"Sam Chong"
    elif order.get("user_id") == 47503940:
        shopify_employee_name = u"Too Shen Chew"
    elif order.get("user_id") == 26202436:
        shopify_employee_name = u"Massimo Hair Lib"

    shopify_employee_name = shopify_employee_name or order.get("user_id")

    create_employee(order.get("user_id"), shopify_employee_name)
    
    so = frappe.db.get_value("Sales Order", {"shopify_id": order.get("id")}, "name")
    if not so:
        so = frappe.get_doc({
            "doctype": "Sales Order",
            "naming_series": shopify_settings.sales_order_series or "SO-Shopify-",
            "shopify_id": order.get("id"),
            "customer": frappe.db.get_value("Customer", {"shopify_id": order.get("customer").get("id")}, "name"),
            "shopify_employee_id": order.get("user_id"),
            "shopify_employee_name": shopify_employee_name,
            "transaction_date": order.get("processed_at"),
            "delivery_date": order.get("processed_at"),
            "selling_price_list": shopify_settings.price_list,
            "ignore_pricing_rule": 1,
            "apply_discount_on": "Net Total",
            "discount_amount": flt(order.get("total_discounts")),
            "items": get_item_line(order.get("line_items"), shopify_settings),
            "taxes": get_tax_line(order, order.get("shipping_lines"), shopify_settings)
        }).insert()
        so.submit()
    else:
        so = frappe.get_doc("Sales Order", so)

        if order.get("financial_status") == "refunded":
            if not frappe.db.sql("""select name from `tabSales Order` where shopify_id = %(shopify_id)s and docstatus = 2""", {"shopify_id": order.get("id")}):
                print "Nothing can do here now"
    return so

def create_sales_invoice(order, shopify_settings, so):
    sales_invoice = frappe.db.get_value("Sales Order", {"shopify_id": order.get("id")},\
         ["ifnull(per_billed, '') as per_billed"], as_dict=1)
         
    if not frappe.db.get_value("Sales Invoice", {"shopify_id": order.get("id")}, "name") and so.docstatus==1 \
        and not sales_invoice["per_billed"]:
        si = make_sales_invoice(so.name)
        si.shopify_id = order.get("id")
        si.naming_series = shopify_settings.sales_invoice_series or "SI-Shopify-"
        si.is_pos = 1
        si.cash_bank_account = shopify_settings.cash_bank_account
        si.submit()

def create_delivery_note(order, shopify_settings, so):  
    for fulfillment in order.get("fulfillments"):
        if not frappe.db.get_value("Delivery Note", {"shopify_id": fulfillment.get("id")}, "name") and so.docstatus==1:
            dn = make_delivery_note(so.name)
            dn.shopify_id = fulfillment.get("id")
            dn.naming_series = shopify_settings.delivery_note_series or "DN-Shopify-"
            dn.items = update_items_qty(dn.items, fulfillment.get("line_items"), shopify_settings)
            dn.save()

def update_items_qty(dn_items, fulfillment_items, shopify_settings):
    return [dn_item.update({"qty": item.get("quantity")}) for item in fulfillment_items for dn_item in dn_items\
         if get_item_code(item) == dn_item.item_code]

def get_discounted_amount(order):
    discounted_amount = 0.0
    for discount in order.get("discount_codes"):
        discounted_amount += flt(discount.get("amount"))
    return discounted_amount
        
def get_item_line(order_items, shopify_settings):
    items = []
    for item in order_items:
        item_code = get_item_code(item)
        items.append({
            "item_code": item_code,
            "item_name": item.get("name"),
            "description": item.get("title") or u"Please refer to the product pics.",
            "rate": item.get("price"),
            "qty": item.get("quantity"),
            "stock_uom": item.get("sku"),
            "warehouse": shopify_settings.warehouse
        })
    return items
    
def get_item_code(item):
    item_code = frappe.db.get_value("Item", {"shopify_id": item.get("variant_id")}, "item_code")
    if not item_code:
        item_code = frappe.db.get_value("Item", {"shopify_id": item.get("product_id")}, "item_code")
    
    return item_code
    
def get_tax_line(order, shipping_lines, shopify_settings):
    taxes = []
    for tax in order.get("tax_lines"):
        taxes.append({
            "charge_type": _("On Net Total"),
            "account_head": get_tax_account_head(tax),
            "description": tax.get("title") + "-" + cstr(tax.get("rate") * 100.00),
            "rate": tax.get("rate") * 100.00,
            "included_in_print_rate": set_included_in_print_rate(order) 
        })
    
    taxes = update_taxes_with_shipping_rule(taxes, shipping_lines)
    
    return taxes

def set_included_in_print_rate(order):
    if order.get("total_tax"): 
        if (flt(order.get("total_price")) - flt(order.get("total_line_items_price"))) == 0.0:
            return 1
    return 0
            
def update_taxes_with_shipping_rule(taxes, shipping_lines):
    for shipping_charge in shipping_lines:
        taxes.append({
            "charge_type": _("Actual"),
            "account_head": get_tax_account_head(shipping_charge),
            "description": shipping_charge["title"],
            "tax_amount": shipping_charge["price"]
        })
        
    return taxes
    
def get_tax_account_head(tax):
    tax_account =  frappe.db.get_value("Shopify Tax Account", \
        {"parent": "Shopify Settings", "shopify_tax": tax.get("title")}, "tax_account")
    
    if not tax_account:
        frappe.throw("Tax Account not specified for Shopify Tax {}".format(tax.get("title")))
    
    return tax_account





