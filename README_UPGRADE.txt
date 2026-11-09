TNPDS · PANRUTI TALUK — FIXED APPLICATION PACKAGE

IMPORTANT DATA INCLUDED
- 201 original FPS shop records from the uploaded Panruti database.
- 17 commodity records from the uploaded database and commodity master file.
- The original master lists are included as shop_master.json and commodity_master.json.
- At startup, the app inserts missing master records with INSERT OR IGNORE. Existing shop/item records and dispatch/allocation records are not deleted or overwritten.

FEATURES
- Shop and commodity add/edit/delete management.
- One FPS shop with multiple commodities saved in one submission.
- Delete an incorrect dispatch quantity row.
- Allocation Excel upload, balances, CSV report, backup and restore.

WINDOWS SETUP
1. Extract this ZIP into a new folder, for example C:\TNPDS_Panruti.
2. Open the folder and double-click run_windows.bat.
3. Open http://127.0.0.1:5000 in your browser.
4. On Daily Dispatch, the shop and commodity dropdowns load from the SQLite database.

UPDATING AN EXISTING INSTALLATION
- First make a backup from the app's Backup page, or copy panruti_tnpds.db to a safe location.
- Copy app.py, shop_master.json, commodity_master.json, and the templates folder into the existing project folder.
- Keep the existing panruti_tnpds.db in that folder. Do not replace it. Startup only inserts missing master records and does not delete dispatch/allocation data.

SOURCE DATA COUNT NOTE
The database and commodity_master.json uploaded with the original project contain 201 shops and 17 commodities (not 18). I have kept every commodity exactly as supplied and have not invented an 18th item. If your earlier application showed an 18th commodity, add its exact name in the Items page after starting the app, or provide its name so it can be included in the master list.


DAILY DISPATCH PRINT (ADDED)
- Open the new “Print Daily Report” menu.
- Select the dispatch date and click “View Report”.
- The report lists every shop/commodity dispatch entry, quantity, challan number and remarks for that date.
- It also shows the number of shops, number of dispatch entries, and commodity-wise daily totals.
- Click “Print / Save as PDF” and choose a printer or “Save as PDF” in the browser print dialog.
- The report uses existing dispatch history and does not modify or delete dispatch records.

UPDATING AN EXISTING INSTALLATION WITHOUT LOSING DATA
- Extract this update ZIP into the SAME folder as your current TNPDS application and allow app.py/templates to be updated.
- IMPORTANT: Keep your existing panruti_tnpds.db file. This update ZIP intentionally does not include or replace the database.
- Do not delete the old application folder or create a fresh database if you need your previous dispatch/allocation history.
- Start the app using run_windows.bat as usual.
