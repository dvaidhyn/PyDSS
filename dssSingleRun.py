from PyQt5.uic.Compiler.qtproxies import i18n_string

import dssInstance
import subprocess
import logging
import os

# Settings for exporting results
RO = {
    'Log Results'    : True,
    'Export Mode'    : 'byClass',           # 'byClass'        , 'byElement'
    'Export Style'   : 'Single file',         # 'Seperate files' , 'Single file'
}
# Plot Settings
PO = {
    'Network layout' : False,
    'Time series'    : False,
    'XY plot'        : False,
    'Sag plot'       : False,
    'Histogram'      : False,
    'GIS overlay'    : False,
}
# Simulation Settings
SS = {
    'Start Day'              : 156,
    'End Day'                : 157,
    'Step resolution (min)'  : 60*4,
    'Max Control Iterations' : 10,
    'Simulation Type'        : 'Daily',
    'Active Project'         : 'HECOHI',
    'Active Scenario'        : 'HP-VV-VW-B2-CSS30',
    'DSS File'               : 'MasterCircuit_Mikilua_baseline2_CSS30.dss',   #'MasterCircuit_Mikilua_keep.dss'Master_HECO19021.dss'
    'Error tolerance'        : 0.1,
}
# Logger settings
LO =  {
    'Logging Level'          : logging.DEBUG,
    'Log to external file'   : True,
    'Display on screen'      : False,
    'Clear old log files'    : True,
}

p = subprocess.Popen(["bokeh", "serve"], stdout=subprocess.PIPE)
DSS = dssInstance.OpenDSS(PlotOptions = PO , ResultOptions=RO, SimulationSettings=SS, LoggerOptions=LO)
DSS.RunSimulation()