/**
 * GETURL - Extract the hyperlink URL from a cell's rich text metadata.
 *
 * When a URL is pasted into Google Sheets and "Replace URL with its title"
 * is clicked, the display text becomes the page title but the original URL
 * is preserved as cell-level rich text link metadata. Standard formulas
 * (REGEXEXTRACT, CELL) cannot access this metadata — only Apps Script can.
 *
 * Usage in a sheet cell:
 *   =GETURL("B2")
 *   =REGEXEXTRACT(GETURL("B"&ROW()), "properties/(\d+)")
 *
 * @param {string} cellAddress  A1-notation reference (e.g. "B2", "C5").
 * @return {string}             The link URL, or empty string if none.
 * @customfunction
 */
function GETURL(cellAddress) {
  var range = SpreadsheetApp.getActiveSheet().getRange(cellAddress);
  var richText = range.getRichTextValue();
  if (richText) {
    var url = richText.getLinkUrl();
    return url || "";
  }
  return "";
}
