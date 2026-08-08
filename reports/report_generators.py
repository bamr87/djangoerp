from datetime import datetime

from celery import shared_task
from django.db.models import Q, Sum

from coa.models import Account
from journal.models import JournalLine


@shared_task
def generate_report(report_id):
    """
    Celery task for asynchronous report generation
    """
    from .models import SavedReport

    try:
        report = SavedReport.objects.get(id=report_id)

        report_type = report.template.report_type
        parameters = report.parameters

        if report_type == 'balance_sheet':
            result = generate_balance_sheet(parameters)
        elif report_type == 'income_statement':
            result = generate_income_statement(parameters)
        elif report_type == 'cash_flow':
            result = generate_cash_flow(parameters)
        elif report_type == 'general_ledger':
            result = generate_general_ledger(parameters)
        elif report_type == 'trial_balance':
            result = generate_trial_balance(parameters)
        else:
            result = generate_custom_report(report.template.configuration, parameters)

        report.result_data = result
        report.status = 'completed'
        report.save()

    except Exception as e:
        if report:
            report.status = 'failed'
            report.error_message = str(e)
            report.save()
        raise


def generate_balance_sheet(parameters):
    """
    Generate a balance sheet report
    """
    as_of_date = parameters.get('as_of_date', datetime.now().strftime('%Y-%m-%d'))

    asset_accounts = Account.objects.filter(account_type__code='AS')
    liability_accounts = Account.objects.filter(account_type__code='LI')
    equity_accounts = Account.objects.filter(account_type__code='EQ')

    assets = calculate_account_balances(asset_accounts, as_of_date)
    liabilities = calculate_account_balances(liability_accounts, as_of_date)
    equity = calculate_account_balances(equity_accounts, as_of_date)

    total_assets = sum(account['balance'] for account in assets)
    total_liabilities = sum(account['balance'] for account in liabilities)
    equity_accounts_total = sum(account['balance'] for account in equity)

    # Revenue and expense accounts stay open until a closing entry moves them to retained
    # earnings, so their net belongs in equity for the statement to foot. Without this the
    # balance sheet never balances for any book that has posted a P&L.
    revenue_accounts = Account.objects.filter(account_type__code='RE')
    expense_accounts = Account.objects.filter(account_type__code='EX')
    total_revenue = sum(a['balance'] for a in calculate_account_balances(revenue_accounts, as_of_date))
    total_expenses = sum(a['balance'] for a in calculate_account_balances(expense_accounts, as_of_date))
    current_period_earnings = total_revenue - total_expenses

    total_equity = equity_accounts_total + current_period_earnings

    return {
        'as_of_date': as_of_date,
        'assets': assets,
        'liabilities': liabilities,
        'equity': equity,
        'total_assets': total_assets,
        'total_liabilities': total_liabilities,
        'equity_accounts_total': equity_accounts_total,
        'current_period_earnings': current_period_earnings,
        'total_equity': total_equity,
        'balanced': abs(total_assets - (total_liabilities + total_equity)) < 0.001
    }


def generate_income_statement(parameters):
    """
    Generate an income statement report
    """
    from_date = parameters.get('from_date')
    to_date = parameters.get('to_date', datetime.now().strftime('%Y-%m-%d'))

    revenue_accounts = Account.objects.filter(account_type__code='RE')
    expense_accounts = Account.objects.filter(account_type__code='EX')

    revenue = calculate_account_balances(revenue_accounts, to_date, from_date)
    expenses = calculate_account_balances(expense_accounts, to_date, from_date)

    total_revenue = sum(account['balance'] for account in revenue)
    total_expenses = sum(account['balance'] for account in expenses)
    net_income = total_revenue - total_expenses

    return {
        'from_date': from_date,
        'to_date': to_date,
        'revenue': revenue,
        'expenses': expenses,
        'total_revenue': total_revenue,
        'total_expenses': total_expenses,
        'net_income': net_income
    }


def generate_trial_balance(parameters):
    """
    Generate a trial balance report
    """
    as_of_date = parameters.get('as_of_date', datetime.now().strftime('%Y-%m-%d'))

    accounts = Account.objects.filter(is_active=True)

    account_balances = []
    total_debits = 0
    total_credits = 0

    for account in accounts:
        debit_sum = JournalLine.objects.filter(
            account=account,
            entry__date__lte=as_of_date,
            entry__status='posted'
        ).aggregate(Sum('debit'))['debit__sum'] or 0

        credit_sum = JournalLine.objects.filter(
            account=account,
            entry__date__lte=as_of_date,
            entry__status='posted'
        ).aggregate(Sum('credit'))['credit__sum'] or 0

        # A trial balance lists raw debit-minus-credit balances: debit-normal accounts
        # land in the debit column, credit-normal ones in the credit column, and the two
        # columns agree. Normalising credit-normal accounts to a positive number first
        # (as this once did) pushed them into the debit column and meant the totals could
        # never match. calculate_account_balances still applies normal-balance signing —
        # that is for the balance sheet and income statement, not for this report.
        balance = debit_sum - credit_sum

        account_balances.append({
            'account_code': account.code,
            'account_name': account.name,
            'account_type': account.account_type.name,
            'debit': max(balance, 0),
            'credit': max(-balance, 0)
        })

        total_debits += max(balance, 0)
        total_credits += max(-balance, 0)

    return {
        'as_of_date': as_of_date,
        'accounts': account_balances,
        'total_debits': total_debits,
        'total_credits': total_credits,
        'balanced': abs(total_debits - total_credits) < 0.001
    }


def generate_general_ledger(parameters):
    """
    Generate a general ledger report
    """
    from_date = parameters.get('from_date')
    to_date = parameters.get('to_date', datetime.now().strftime('%Y-%m-%d'))
    account_id = parameters.get('account_id')

    query = Q(entry__status='posted')

    if from_date:
        query &= Q(entry__date__gte=from_date)

    if to_date:
        query &= Q(entry__date__lte=to_date)

    if account_id:
        query &= Q(account_id=account_id)
        accounts = Account.objects.filter(id=account_id)
    else:
        accounts = Account.objects.filter(is_active=True)

    ledger = {}
    for account in accounts:
        opening_debit = JournalLine.objects.filter(
            account=account,
            entry__date__lt=from_date if from_date else '1900-01-01',
            entry__status='posted'
        ).aggregate(Sum('debit'))['debit__sum'] or 0

        opening_credit = JournalLine.objects.filter(
            account=account,
            entry__date__lt=from_date if from_date else '1900-01-01',
            entry__status='posted'
        ).aggregate(Sum('credit'))['credit__sum'] or 0

        opening_balance = opening_debit - opening_credit

        lines = JournalLine.objects.filter(query, account=account).order_by('entry__date', 'entry__id')

        transactions = []
        balance = opening_balance

        for line in lines:
            balance += line.debit - line.credit
            transactions.append({
                'date': line.entry.date.strftime('%Y-%m-%d'),
                'entry_number': line.entry.entry_number,
                'description': line.entry.description,
                'reference': line.reference,
                'debit': line.debit,
                'credit': line.credit,
                'balance': balance
            })

        ledger[account.code] = {
            'account_code': account.code,
            'account_name': account.name,
            'account_type': account.account_type.name,
            'opening_balance': opening_balance,
            'transactions': transactions,
            'ending_balance': balance
        }

    return {
        'from_date': from_date,
        'to_date': to_date,
        'ledger': ledger
    }


def generate_cash_flow(parameters):
    """
    Generate a cash flow statement
    """
    from_date = parameters.get('from_date')
    to_date = parameters.get('to_date', datetime.now().strftime('%Y-%m-%d'))

    # This is a simplified cash flow calculation
    # A real implementation would require more detailed categorization
    cash_accounts = Account.objects.filter(code__startswith='101')  # Assuming 101 is cash

    operating_activities = []
    operating_total = 0

    investing_activities = []
    investing_total = 0

    financing_activities = []
    financing_total = 0

    beginning_cash = 0
    for account in cash_accounts:
        debit_sum = JournalLine.objects.filter(
            account=account,
            entry__date__lt=from_date if from_date else '1900-01-01',
            entry__status='posted'
        ).aggregate(Sum('debit'))['debit__sum'] or 0

        credit_sum = JournalLine.objects.filter(
            account=account,
            entry__date__lt=from_date if from_date else '1900-01-01',
            entry__status='posted'
        ).aggregate(Sum('credit'))['credit__sum'] or 0

        beginning_cash += debit_sum - credit_sum

    ending_cash = beginning_cash + operating_total + investing_total + financing_total

    return {
        'from_date': from_date,
        'to_date': to_date,
        'operating_activities': operating_activities,
        'operating_total': operating_total,
        'investing_activities': investing_activities,
        'investing_total': investing_total,
        'financing_activities': financing_activities,
        'financing_total': financing_total,
        'beginning_cash': beginning_cash,
        'ending_cash': ending_cash,
    }


def generate_custom_report(configuration, parameters):
    """
    Generate a custom report based on template configuration
    """
    # Custom report generation logic would go here
    # This is a placeholder that returns the parameters and configuration
    return {
        'type': 'custom',
        'configuration': configuration,
        'parameters': parameters,
        'results': {
            'message': 'Custom report generation not fully implemented'
        }
    }


def calculate_account_balances(accounts, as_of_date, from_date=None):
    """
    Helper function to calculate balances for a list of accounts
    """
    results = []

    for account in accounts:
        query = Q(account=account, entry__status='posted')

        if as_of_date:
            query &= Q(entry__date__lte=as_of_date)

        if from_date:
            query &= Q(entry__date__gte=from_date)

        debit_sum = JournalLine.objects.filter(query).aggregate(Sum('debit'))['debit__sum'] or 0
        credit_sum = JournalLine.objects.filter(query).aggregate(Sum('credit'))['credit__sum'] or 0

        # For asset and expense accounts, debit increases the balance
        # For liability, equity, and revenue accounts, credit increases the balance
        if account.account_type.code in ['AS', 'EX']:
            balance = debit_sum - credit_sum
        else:
            balance = credit_sum - debit_sum

        results.append({
            'id': account.id,
            'code': account.code,
            'name': account.name,
            'balance': balance
        })

    return results
