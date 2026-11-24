/*
 * Built-in sample file for "Try it with sample data". Public places in the
 * Greater Chattanooga region only (city halls, hospitals, schools, landmarks;
 * no client data), so the results map shows one compact area. The first 30
 * were matched by the Census geocoder when this list was built, several of
 * them downtown so the map shows a range of counts. S31 is a PO box (never
 * matched) and S32 is a made-up address, so the fix-and-retry step has
 * something to show: correct it to "615 McCallie Ave" and retry.
 */
(function (root) {
  "use strict";
  const columns = ["client_id", "street", "city", "state", "zip"];
  const data = [
    ["S01", "1400 Market St", "Chattanooga", "TN", "37402"],
    ["S02", "1 Broad St", "Chattanooga", "TN", "37402"],
    ["S03", "10 Bluff View Ave", "Chattanooga", "TN", "37403"],
    ["S04", "615 McCallie Ave", "Chattanooga", "TN", "37403"],
    ["S05", "975 E 3rd St", "Chattanooga", "TN", "37403"],
    ["S06", "921 E 3rd St", "Chattanooga", "TN", "37403"],
    ["S07", "101 E 11th St", "Chattanooga", "TN", "37402"],
    ["S08", "625 Georgia Ave", "Chattanooga", "TN", "37402"],
    ["S09", "1001 Broad St", "Chattanooga", "TN", "37402"],
    ["S10", "1150 Carter St", "Chattanooga", "TN", "37402"],
    ["S11", "1250 Market St", "Chattanooga", "TN", "37402"],
    ["S12", "200 River St", "Chattanooga", "TN", "37405"],
    ["S13", "301 N Holtzclaw Ave", "Chattanooga", "TN", "37404"],
    ["S14", "2525 De Sales Ave", "Chattanooga", "TN", "37404"],
    ["S15", "2333 McCallie Ave", "Chattanooga", "TN", "37404"],
    ["S16", "5600 Brainerd Rd", "Chattanooga", "TN", "37411"],
    ["S17", "2100 Hamilton Place Blvd", "Chattanooga", "TN", "37421"],
    ["S18", "7325 Shallowford Rd", "Chattanooga", "TN", "37421"],
    ["S19", "1720 S Scenic Hwy", "Chattanooga", "TN", "37409"],
    ["S20", "3917 St Elmo Ave", "Chattanooga", "TN", "37409"],
    ["S21", "1517 Tombras Ave", "East Ridge", "TN", "37412"],
    ["S22", "3117 Dayton Blvd", "Red Bank", "TN", "37415"],
    ["S23", "1111 Ridgeway Ave", "Signal Mountain", "TN", "37377"],
    ["S24", "4910 Swinyar Dr", "Collegedale", "TN", "37315"],
    ["S25", "9835 Dayton Pike", "Soddy Daisy", "TN", "37379"],
    ["S26", "155 N Ocoee St", "Cleveland", "TN", "37311"],
    ["S27", "1400 Patten Rd", "Lookout Mountain", "GA", "30750"],
    ["S28", "400 McFarland Ave", "Rossville", "GA", "30741"],
    ["S29", "500 City Hall Dr", "Fort Oglethorpe", "GA", "30742"],
    ["S30", "103 S Duke St", "LaFayette", "GA", "30728"],
    ["S31", "PO Box 100", "Chattanooga", "TN", "37401"],
    ["S32", "99 Nowhere Lane", "Chattanooga", "TN", "37403"],
  ];
  root.A2TSample = {
    fileName: "sample_addresses.xlsx",
    columns,
    rows: data.map((r) => Object.fromEntries(columns.map((c, i) => [c, r[i]]))),
  };
})(typeof self !== "undefined" ? self : this);
