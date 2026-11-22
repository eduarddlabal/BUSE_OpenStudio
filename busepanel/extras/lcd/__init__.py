"""Add-on: LCD panel in the PID style as a web page (line, zone, next stops with minutes, clock, terminal).

Not a BUSE panel – an extra output of the simulation, e.g. for OMSI or a tablet in the vehicle.
Hooks into the core:
  * sim/vehicle.py   – output kind "lcd" (make_output) -> output.LcdOutput, which takes no telegrams
  * server/sim_api.py – after a vehicle rebuild: server.LCD.sync() runs one HTTP server per LCD port
  * server/app.py    – the same page on the main port at /lcd/
Files: view.py (state -> JSON), output.py, server.py, web/ (page).
"""
