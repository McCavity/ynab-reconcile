# YNAB Reconciliation Tool

An interactive command-line tool for reconciling [YNAB](https://www.youneedabudget.com/) transactions against bank statements. The interface is in German.

## Features

- **Interactive matching** – compares uncleared YNAB transactions against a bank CSV export (Finanzblick format), using a weighted scoring algorithm (amount, date, payee similarity)
- **Finanzblick CSV import** – reads exported `Buchungsliste.csv` files directly; correctly extracts real merchant names from PayPal transactions
- **Payee aliases** – remembers mappings between bank payee names and YNAB payee names (e.g. "Lieferando.de" → "Takeaway.com") for better future matching
- **Deferred / aggregated payees** – payees like public transit (RMV) that are billed monthly as a lump sum can be marked as "always skip", so individual YNAB entries are automatically ignored during reconciliation
- **New transaction dialog** – when a bank transaction has no YNAB counterpart, create one directly with fuzzy payee search, category picker, editable date, and a save/redo/cancel confirmation
- **Reconcile** – after clearing transactions, compares YNAB cleared balance against the actual bank balance; if they match, marks all cleared transactions as `reconciled`; if not, optionally creates a balance adjustment
- **Multi-account loop** – after finishing one account, refreshes the list and offers the next account without restarting the script

## Requirements

- Python 3.9 or newer (no third-party packages required – stdlib only)
- A YNAB account with API access
- A YNAB Personal Access Token

## Setup

1. **Clone the repository**

   ```bash
   git clone https://github.com/youruser/ynab-reconcile.git
   cd ynab-reconcile
   ```

2. **Create your `.env` file**

   ```bash
   cp .env.example .env
   ```

   Open `.env` and replace the placeholder with your actual token:

   ```
   YNAB_API_TOKEN=your_token_here
   ```

   To obtain a token: log in to YNAB → *Account Settings* → *Developer Settings* → *New Token*.

3. **Run the connection test** *(optional but recommended)*

   ```bash
   python3 ynab_test.py
   ```

   This fetches your budgets and accounts to confirm that the API connection works.

## Usage

```bash
python3 ynab_reconcile.py
```

The tool will:

1. Load your budget (or ask you to choose one if you have several)
2. Show all accounts with uncleared transactions
3. Ask you to select an account
4. Show uncleared YNAB transactions for that account
5. Ask for a bank CSV file (or manual paste)
6. Run the matching algorithm and display results
7. Walk you through each transaction interactively

### Actions during reconciliation

| Situation | Indicator | Available actions |
|---|---|---|
| YNAB ↔ Bank match | ✅ / ⚠️ | `[c]`lear, `[e]`dit amount, `[s]`kip |
| Only in YNAB | 📋 | `[s]`kip (keep open), `[c]`lear anyway, `[i]`always skip (deferred) |
| Only in bank | 🏦 | `[a]`dd to YNAB, `[s]`kip |

### Bank CSV format (Finanzblick)

Export a *Buchungsliste* from [Finanzblick](https://www.finanzblick.de/) as CSV and place it in the same folder as the script. The tool detects it automatically. The expected column structure is:

```
Buchungsdatum;Wertstellungsdatum;Empfaenger;Verwendungszweck;Buchungstext;
Betrag;IBAN;BIC;Kategorie;Konto;Umbuchung;Notiz;Schlagworte;SteuerKategorie;
ParentKategorie;AbweichenderEmpfaenger;Splitbuchung;Auswertungsdatum
```

### Persistent data files

The tool creates two files in the script directory that are excluded from version control:

| File | Contents |
|---|---|
| `aliases.json` | Bank payee → YNAB payee name mappings |
| `config.json` | Deferred payee keywords (for aggregated billers) |

These are generated automatically during use. You can safely delete them to reset all learned mappings.

## Project structure

```
.
├── ynab_reconcile.py   # Main reconciliation tool
├── ynab_test.py        # API connection test
├── .env.example        # Template for API credentials
├── .gitignore
├── LICENSE
└── README.md
```

## License

MIT – see [LICENSE](LICENSE).
