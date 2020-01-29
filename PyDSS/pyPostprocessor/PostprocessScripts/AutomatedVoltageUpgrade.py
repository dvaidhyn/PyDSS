import os
import matplotlib.pyplot as plt
import opendssdirect as dss
import networkx as nx
import time
import numpy as np
import seaborn as sns
from sklearn.cluster import AgglomerativeClustering
import json

from  PyDSS.pyPostprocessor.pyPostprocessAbstract import AbstractPostprocess
from PyDSS.pyPostprocessor.PostprocessScripts.postprocess_voltage_upgrades import postprocess_voltage_upgrades

plt.rcParams.update({'font.size': 14})

class AutomatedVoltageUpgrade(AbstractPostprocess):
    """The class is used to induce faults on bus for dynamic simulation studies. Subclass of the :class:`PyDSS.pyControllers.pyControllerAbstract.ControllerAbstract` abstract class. 

    :param FaultObj: A :class:`PyDSS.dssElement.dssElement` object that wraps around an OpenDSS 'Fault' element
    :type FaultObj: class:`PyDSS.dssElement.dssElement`
    :param Settings: A dictionary that defines the settings for the faul controller.
    :type Settings: dict
    :param dssInstance: An :class:`opendssdirect` instance
    :type dssInstance: :class:`opendssdirect` instance
    :param ElmObjectList: Dictionary of all dssElement, dssBus and dssCircuit ojects
    :type ElmObjectList: dict
    :param dssSolver: An instance of one of the classes defined in :mod:`PyDSS.SolveMode`.
    :type dssSolver: :mod:`PyDSS.SolveMode`
    :raises: AssertionError  if 'FaultObj' is not a wrapped OpenDSS Fault element

    """
    def __init__(self, dssInstance, dssSolver, dssObjects, dssObjectsByClass, simulationSettings, Logger):
        """Constructor method
        """
        self.Settings = simulationSettings
        super(AutomatedVoltageUpgrade).__init__()
        self.__dssinstance = dssInstance
        New_settings = {
            "Feeder": "../Test_Feeder_RIN_69_08_2030_sb100",
            "img_path": "../Images",
            "DPV_scenarios": "../ten_random_RIN_69_08_2030_sb100",
            "master file": "MasterDisco.dss",
            "DPV_penetration_HClimit": 0,
            "DPV_penetration_target": 200,
            "DPV control": "PF=1",  # "PF=1" or "PF=-0.95" or "VVar-CatA" or "VVar-CatB" or "VVar-VWatt-CatB"
            "DPV system priority": "watt",  # "watt" or "var"
            "Outputs": "C:\Documents_NREL\Grid_Cost_DER_PhaseII\Control_device_placement\Outputs",
            "V_upper_lim": 1.05,
            "V_lower_lim": 0.95,
            "Target_V": 1,
            "plot window open time": 1,  # seconds
            "Min PVLoad multiplier": 1,
            "Min Load multiplier": 0.1,
            "Max Load multiplier": 1,
            "Range B upper": 1.05,
            "Range B lower": 0.95,
            "nominal_voltage": 120,
            "nominal pu voltage": 1,
            "tps_to_test": [0.2, 1.2, 0.1, 0.9],
            # [min load multiplier without PV, max load multiplier without PV, min load multiplier with PV, max load multiplier with PV]
            "create topology plots": True,
            # Set this to true only if coordinates for all buses in the network are available
            "Cap sweep voltage gap": 1,
            # This value determines difference increase between cap ON and OFF voltage setting, example (119.5, 120.5), (119,121) and so on
            "max control iterations": 50,  # max OpenDSS Control iteration limit
            "reg control bands": [1, 2],  # Reg control voltage bands within which taps will not change
            "reg v delta": 0.5,  # Reg control voltage set point is varied in this range
            "Max regulators": 4,
            # This number gives the maximum number of regulators placed in the feeder apart from substation LTC
            "Use LTC placement": True,
        }
        for key,val in New_settings.items():
            if key not in self.Settings:
                self.Settings[key] = val
        self.logger = Logger
        dss = dssInstance
        self.dssSolver = dssSolver
        self.start = time.time()
        # Cap bank default settings -
        self.capON = round((self.Settings["nominal_voltage"] - self.Settings["Cap sweep voltage gap"] / 2), 1)
        self.capOFF = round((self.Settings["nominal_voltage"] + self.Settings["Cap sweep voltage gap"] / 2), 1)
        self.capONdelay = 0
        self.capOFFdelay = 0
        self.capdeadtime = 0
        self.PTphase = "AVG"
        self.cap_control = "voltage"
        self.max_regs = self.Settings["Max regulators"]
        self.terminal = 1

        # TODO: Regs default settings

        # Substation LTC default settings
        self.LTC_setpoint = 1.03 * self.Settings["nominal_voltage"]
        self.LTC_wdg = 2
        self.LTC_delay = 45  # in seconds
        self.LTC_band = 2  # deadband in volts

        # Initialize dss upgrades file
        self.dss_upgrades = [
            "//This file has all the upgrades determined using the control device placement algorithm \n"]

        self.dssSolver = dss.Solution

        # Get correct source bus and conn type for all downstream regs - since regs are added before DTs and after
        # sub xfmr - their connection should be same as that of secondary wdg of sub xfmr
        dss.Vsources.First()
        self.source = dss.CktElement.BusNames()[0].split(".")[0]
        self.reg_conn = "wye"
        dss.Transformers.First()
        while True:
            xfmr_buses = dss.CktElement.BusNames()
            for bus in xfmr_buses:
                if bus.split(".")[0].lower() == self.source:
                    num_wdgs = dss.Transformers.NumWindings()
                    for wdg in range(0, num_wdgs, 1):
                        self.reg_conn = dss.Properties.Value("conn")
            if not dss.Transformers.Next() > 0:
                break

        start_t = time.time()
        self.generate_nx_representation()

        self.get_existing_controller_info()
        if self.Settings["create topology plots"]:
            # self.plot_feeder()
            pass
        self.write_flag = 1

        # Use this block for capacitor settings
        self.check_voltage_violations_multi_tps()
        print("Initial number of buses with violations are: ", len(self.buses_with_violations))
        print("Initial objective function value: ", self.severity_indices[2])
        print("Initial maximum voltage observed on any node:", self.max_V_viol)
        print("Initial minimum voltage observed on any node:", self.min_V_viol)
        if len(self.buses_with_violations) > 0:
            if self.Settings["create topology plots"]:
                self.plot_violations()
            # Correct cap banks settings if caps are present in the feeder
            if dss.Capacitors.Count() > 0:
                self.get_capacitor_state()
                self.correct_cap_bank_settings()
                if self.Settings["create topology plots"]:
                    self.plot_violations()
                if len(self.buses_with_violations) > 0:
                    self.cap_settings_sweep()
                self.check_voltage_violations_multi_tps()
                if self.Settings["create topology plots"]:
                    self.plot_violations()
            else:
                print("\n", "No cap banks exist in the system")

        # Do a settings sweep of existing reg control devices (other than sub LTC) after correcting their other
        #  parameters such as ratios etc
        self.check_voltage_violations_multi_tps()
        self.reg_sweep_viols = {}
        if dss.RegControls.Count() > 0 and len(self.buses_with_violations) > 0:
            self.initial_regctrls_settings = {}
            dss.RegControls.First()
            while True:
                name = dss.RegControls.Name()
                xfmr = dss.RegControls.Transformer()
                dss.Circuit.SetActiveElement("Transformer.{}".format(xfmr))
                xfmr_buses = dss.CktElement.BusNames()
                xfmr_b1 = xfmr_buses[0].split(".")[0]
                xfmr_b2 = xfmr_buses[1].split(".")[0]
                # # Skipping over substation LTC if it exists
                # for n, d in self.G.in_degree().items():
                #     if d==0:
                #         sourcebus = n
                sourcebus = self.source
                if xfmr_b1.lower() == sourcebus.lower() or xfmr_b2.lower() == sourcebus.lower():
                    dss.Circuit.SetActiveElement("Regcontrol.{}".format(name))
                    # dss.RegControls.Next()
                    if not dss.RegControls.Next() > 0:
                        break
                    continue
                dss.Circuit.SetActiveElement("Regcontrol.{}".format(name))
                bus_name = dss.CktElement.BusNames()[0].split(".")[0]
                dss.Circuit.SetActiveBus(bus_name)
                phases = dss.CktElement.NumPhases()
                kV = dss.Bus.kVBase()
                dss.Circuit.SetActiveElement("Regcontrol.{}".format(name))
                winding = self.LTC_wdg
                reg_delay = self.LTC_delay
                pt_ratio = kV * 1000 / (self.Settings["nominal_voltage"])
                try:
                    Vreg = dss.Properties.Value("vreg")
                except:
                    Vreg = self.Settings["nominal_voltage"]
                try:
                    bandwidth = dss.Properties.Value("band")
                except:
                    bandwidth = 3.0
                self.initial_regctrls_settings[name] = [Vreg, bandwidth]
                command_string = "Edit RegControl.{rn} transformer={tn} winding={w} vreg={sp} ptratio={rt} band={b} " \
                                 "enabled=true delay={d} !original".format(
                    rn=name,
                    tn=xfmr,
                    w=winding,
                    sp=Vreg,
                    rt=pt_ratio,
                    b=bandwidth,
                    d=reg_delay
                )
                dss.run_command(command_string)
                self.dssSolver.Solve()
                # add this to a dss_upgrades.dss file
                self.write_dss_file(command_string)
                if not dss.RegControls.Next() > 0:
                    break
            self.check_voltage_violations_multi_tps()
            self.reg_sweep_viols["original"] = self.severity_indices[2]
            if len(self.buses_with_violations) > 0:
                self.reg_controls_sweep()
                self.check_voltage_violations_multi_tps()
                if self.Settings["create topology plots"]:
                    self.plot_violations()

        # New devices might be added after this
        self.check_voltage_violations_multi_tps()
        self.cluster_optimal_reg_nodes = {}
        self.cluster_optimal_reg_nodes["pre_reg"] = [self.severity_indices[2]]
        self.sub_LTC_added_flag = 0

        # Use this block for adding a substation LTC, correcting its settings and running a sub LTC settings sweep -
        # if LTC exists first try to correct its non set point simulation settings - if this does not correct everything
        #  correct its set points through a sweep - if LTC does not exist add one including a xfmr if required - then
        #  do a settings sweep if required
        # self.add_ctrls_flag = 0
        # TODO: If adding a substation LTC increases violations even after the control sweep then before then remove it
        # TODO: - this might however interfere with voltage regulator logic so may be let it be there
        if self.Settings["Use LTC placement"]:
            self.check_voltage_violations_multi_tps()
            if len(self.buses_with_violations) > 0:
                if self.Settings["create topology plots"]:
                    self.plot_violations()
                # Add substation LTC if not available (may require addition of a new substation xfmr as well)
                # if available correct its settings
                self.subLTC_sweep_viols = {}
                self.LTC_exists_flag = 0
                if dss.RegControls.Count() > 0 and len(self.buses_with_violations) > 0:
                    self.initial_subLTC_settings = {}
                    dss.RegControls.First()
                    while True:
                        name = dss.RegControls.Name()
                        xfmr = dss.RegControls.Transformer()
                        dss.Circuit.SetActiveElement("Transformer.{}".format(xfmr))
                        xfmr_buses = dss.CktElement.BusNames()
                        xfmr_b1 = xfmr_buses[0].split(".")[0]
                        xfmr_b2 = xfmr_buses[1].split(".")[0]
                        # for n, d in self.G.in_degree().items():
                        #     if d==0:
                        #         sourcebus = n
                        sourcebus = self.source
                        # Skipping over all reg controls other than sub LTC
                        if xfmr_b1.lower() == sourcebus.lower() or xfmr_b2.lower() == sourcebus.lower():
                            self.LTC_exists_flag = 1
                            dss.Circuit.SetActiveElement("Regcontrol.{}".format(name))
                            bus_name = dss.CktElement.BusNames()[0].split(".")[0]
                            dss.Circuit.SetActiveBus(bus_name)
                            phases = dss.CktElement.NumPhases()
                            kV = dss.Bus.kVBase()
                            winding = self.LTC_wdg
                            reg_delay = self.LTC_delay
                            pt_ratio = kV * 1000 / (self.Settings["nominal_voltage"])
                            try:
                                Vreg = dss.Properties.Value("vreg")
                            except:
                                Vreg = self.Settings["nominal_voltage"]
                            try:
                                bandwidth = dss.Properties.Value("band")
                            except:
                                bandwidth = 3.0
                            self.initial_subLTC_settings[name] = [Vreg, bandwidth]
                            command_string = "Edit RegControl.{rn} transformer={tn} winding={w} vreg={sp} ptratio={rt} band={b} " \
                                             "enabled=true delay={d} !original".format(
                                rn=name,
                                tn=xfmr,
                                w=winding,
                                sp=Vreg,
                                rt=pt_ratio,
                                b=bandwidth,
                                d=reg_delay
                            )
                            dss.run_command(command_string)
                            self.dssSolver.Solve()
                            # add this to a dss_upgrades.dss file
                            self.write_dss_file(command_string)
                            dss.Circuit.SetActiveElement("Regcontrol.{}".format(name))
                        if not dss.RegControls.Next() > 0:
                            break
                    if self.LTC_exists_flag == 1:
                        self.check_voltage_violations_multi_tps()
                        self.subLTC_sweep_viols["original"] = self.severity_indices[2]
                        if len(self.buses_with_violations) > 0:
                            self.LTC_controls_sweep()
                            self.check_voltage_violations_multi_tps()
                            if self.Settings["create topology plots"]:
                                self.plot_violations()
                    elif self.LTC_exists_flag == 0:
                        self.add_substation_LTC()
                        self.check_voltage_violations_multi_tps()
                        self.cluster_optimal_reg_nodes["sub_LTC"] = [self.severity_indices[2]]
                        self.sub_LTC_added_flag = 1
                        if len(self.buses_with_violations) > 0:
                            self.LTC_controls_sweep()
                            self.check_voltage_violations_multi_tps()
                            if self.Settings["create topology plots"]:
                                self.plot_violations()
                elif dss.RegControls.Count() == 0 and len(self.buses_with_violations) > 0:
                    self.add_substation_LTC()
                    self.check_voltage_violations_multi_tps()
                    self.cluster_optimal_reg_nodes["sub_LTC"] = [self.severity_indices[2]]
                    self.sub_LTC_added_flag = 1
                    if len(self.buses_with_violations) > 0:
                        self.LTC_controls_sweep()
                        self.check_voltage_violations_multi_tps()
                        if self.Settings["create topology plots"]:
                            self.plot_violations()

        # Correct regulator settings if regs are present in the feeder other than the sub station LTC
        # TODO: Remove regs of last iteration
        self.check_voltage_violations_multi_tps()
        if len(self.buses_with_violations) > 1:
            if self.Settings["create topology plots"]:
                self.plot_violations()
            # for n, d in self.G.in_degree().items():
            #     if d == 0:
            #         self.source = n
            # Place additional regulators if required
            self.max_regs = int(min(self.Settings["Max regulators"], len(self.buses_with_violations)))
            self.get_shortest_path()
            self.get_full_distance_dict()
            self.cluster_square_array()
            min_severity = pow(len(self.all_bus_names), 2) * len(self.Settings["tps_to_test"]) * self.Settings[
                "Range B upper"]
            min_cluster = ''
            for key, vals in self.cluster_optimal_reg_nodes.items():
                if vals[0] < min_severity:
                    min_severity = vals[0]
                    min_cluster = key
            # Logic is if violations were less before addition of any device, revert back to that condition by removing
            #  added LTC and not adding best determined in-line regs, else if LTC was best - pass and do not add new
            #  in-line regs, or if some better LTC placemenr was determined apply that
            if min_cluster == "pre_reg":
                # This will remove LTC controller, but if initially there was no substation transformer
                # (highly unlikely) the added transformer will still be there
                if self.sub_LTC_added_flag == 1:
                    if self.subxfmr == '':
                        LTC_reg_node = self.source
                    elif self.subxfmr != '':
                        LTC_reg_node = self.sub_LTC_bus
                    LTC_name = "New_regctrl_" + LTC_reg_node
                    command_string = "Edit RegControl.{ltc_nm} enabled=False".format(
                        ltc_nm=LTC_name
                    )
                    dss.run_command(command_string)
                    self.write_dss_file(command_string)
                else:
                    pass
            elif min_cluster == "sub_LTC":
                pass
            else:
                for reg_nodes in self.cluster_optimal_reg_nodes[min_cluster][2]:
                    self.write_flag = 1
                    self.add_new_xfmr(reg_nodes)
                    self.add_new_regctrl(reg_nodes)
                    rn_name = "New_regctrl_" + reg_nodes
                    command_string = "Edit RegControl.{rn} vreg={vsp} band={b}".format(
                        rn=rn_name,
                        vsp=self.cluster_optimal_reg_nodes[min_cluster][1][0],
                        b=self.cluster_optimal_reg_nodes[min_cluster][1][1]
                    )
                    dss.run_command(command_string)
                    dss.run_command("CalcVoltageBases")
                    self.write_dss_file(command_string)
                self.write_dss_file("CalcVoltageBases")
                # After all additional devices have been placed perform the cap bank settings sweep again-
                # only if new devices were accepted

                self.check_voltage_violations_multi_tps()
                if dss.CapControls.Count() > 0 and len(self.buses_with_violations) > 0:
                    self.cap_settings_sweep()
                    self.check_voltage_violations_multi_tps()
                    if self.Settings["create topology plots"]:
                        self.plot_violations()

            self.check_voltage_violations_multi_tps()
            print("Compare objective with best and applied settings, ", self.cluster_optimal_reg_nodes[min_cluster][0],
                  self.severity_indices[2])
            print("Additional regctrl  devices: ", min_cluster)
            print(self.cluster_optimal_reg_nodes)

        end_t = time.time()
        self.check_voltage_violations_multi_tps()
        if self.Settings["create topology plots"]:
            self.plot_violations()
        self.write_upgrades_to_file()
        print("total_time = ", end_t - start_t)
        postprocess_voltage_upgrades({"outputs": self.Settings["Outputs"]})
        # # TODO: Check impact of upgrades - Cannot recompile feeder in PyDSS
        # print("Checking impact of redirected upgrades file")
        # upgrades_file = os.path.join(self.Settings["Outputs"], "Voltage_upgrades.dss")
        # dss.run_command("Redirect {}".format(upgrades_file))
        # self.dssSolver.Solve()
        # self.check_voltage_violations_multi_tps()
        # if self.Settings["create topology plots"]:
        #     self.plot_violations()
        print("Final number of buses with violations are: ", len(self.buses_with_violations))
        print("Final objective function value: ", self.severity_indices[2])
        print("Final maximum voltage observed on any node:", self.max_V_viol, self.busvmax)
        print("Final minimum voltage observed on any node:", self.min_V_viol)

    def get_existing_controller_info(self):
        self.cap_control_info = {}
        self.reg_control_info = {}

        cap_bank_list = []
        if dss.CapControls.Count() > 0:
            dss.CapControls.First()
            while True:
                cap_ctrl = dss.CapControls.Name().lower()
                cap_name = dss.CapControls.Capacitor().lower()
                cap_bank_list.append(cap_name)
                ctrl_type = dss.Properties.Value("type")
                on_setting = dss.CapControls.ONSetting()
                off_setting = dss.CapControls.OFFSetting()
                dss.Capacitors.Name(cap_name)
                if not cap_name.lower() == dss.Capacitors.Name().lower():
                    print("Incorrect Active Element")
                    quit()
                cap_size = dss.Capacitors.kvar()
                cap_kv = dss.Capacitors.kV()
                self.cap_control_info[cap_ctrl] = {
                    "cap_name": cap_name,
                    "cap kVAR": cap_size,
                    "cap_kv": cap_kv,
                    "Control type": ctrl_type,
                    "ON": on_setting,
                    "OFF": off_setting
                }
                dss.Circuit.SetActiveElement("CapControl.{}".format(cap_ctrl))
                if not dss.CapControls.Next() > 0:
                    break

        if dss.RegControls.Count() > 0:
            dss.RegControls.First()
            while True:
                reg_ctrl = dss.RegControls.Name().lower()
                reg_vsp = dss.Properties.Value("vreg")
                reg_band = dss.Properties.Value("band")
                xfmr_name = dss.RegControls.Transformer().lower()
                dss.Transformers.Name(xfmr_name)
                if not xfmr_name.lower() == dss.Transformers.Name().lower():
                    print("Incorrect Active Element")
                    quit()
                xfmr_buses = dss.CktElement.BusNames()
                # bus_names = []
                # for buses in xfmr_buses:
                #     bus_names.append(buses.split(".")[0].lower())
                # if self.source.lower() in bus_names:
                #     self.sub_xfmr_cap = 1
                xfmr_size = dss.Transformers.kVA()
                xfmr_kv = dss.Transformers.kV()
                self.reg_control_info[reg_ctrl] = {
                    "reg_vsp": reg_vsp,
                    "reg_band": reg_band,
                    "xfmr_name": xfmr_name,
                    "xfmr kVA": xfmr_size,
                    "xfmr_kv": xfmr_kv
                }
                dss.Circuit.SetActiveElement("RegControl.{}".format(reg_ctrl))
                if not dss.RegControls.Next() > 0:
                    break

        # if self.sub_xfmr_cap==0:
        dss.Transformers.First()
        while True:
            bus_names = dss.CktElement.BusNames()
            bus_names_only = []
            for buses in bus_names:
                bus_names_only.append(buses.split(".")[0].lower())
            if self.source.lower() in bus_names_only:
                sub_xfmr = dss.Transformers.Name()
                self.reg_control_info["orig_substation_xfmr"] = {
                    "xfmr_name": sub_xfmr,
                    "xfmr kVA": dss.Transformers.kVA(),
                    "xfmr_kv": dss.Transformers.kV(),
                    "bus_names": bus_names_only
                }
            if not dss.Transformers.Next() > 0:
                break

        if dss.Capacitors.Count() > 0:
            dss.Capacitors.First()
            while True:
                cap_name = dss.Capacitors.Name().lower()
                cap_size = dss.Capacitors.kvar()
                cap_kv = dss.Capacitors.kV()
                ctrl_type = "NA"
                if cap_name not in cap_bank_list:
                    self.cap_control_info["capbank_noctrl_{}".format(cap_name)] = {
                        "cap_name": cap_name,
                        "cap kVAR": cap_size,
                        "cap_kv": cap_kv,
                        "Control type": ctrl_type
                    }
                if not dss.Capacitors.Next() > 0:
                    break

        self.write_to_json(self.cap_control_info, "Initial_capacitors")
        self.write_to_json(self.reg_control_info, "Initial_regulators")

    def write_to_json(self, dict, file_name):
        with open(os.path.join(self.Settings["Outputs"], "{}.json".format(file_name)), "w") as fp:
            json.dump(dict, fp, indent=4)

    def generate_nx_representation(self):
        self.all_bus_names = dss.Circuit.AllBusNames()
        self.G = nx.DiGraph()
        self.generate_nodes()
        self.generate_edges()
        self.pos_dict = nx.get_node_attributes(self.G, 'pos')
        if self.Settings["create topology plots"]:
            self.correct_node_coords()

    def correct_node_coords(self):
        # If node doesn't have node attributes, attach parent or child node's attributes
        new_temp_graph = self.G
        temp_graph = new_temp_graph.to_undirected()
        # for n, d in self.G.in_degree().items():
        #     if d == 0:
        #         self.source = n
        for key, vals in self.pos_dict.items():
            if vals[0] == 0.0 and vals[1] == 0.0:
                new_x = 0
                new_y = 0
                pred_buses = nx.shortest_path(temp_graph, source=key, target=self.source)
                if len(pred_buses) > 0:
                    for pred_bus in pred_buses:
                        if pred_bus == key:
                            continue
                        if self.pos_dict[pred_bus][0] != 0.0 and self.pos_dict[pred_bus][1] != 0.0:
                            new_x = self.pos_dict[pred_bus][0]
                            new_y = self.pos_dict[pred_bus][1]
                            self.G.node[key]["pos"] = [new_x, new_y]
                            break
                if new_x == 0 and new_y == 0:
                    # Since either predecessor nodes were not available or they did not have
                    # non-zero coordinates, try successor nodes
                    # Get a leaf node
                    for x in self.G.nodes():
                        if self.G.out_degree(x) == 0 and self.G.in_degree(x) == 1:
                            leaf_node = x
                            break
                    succ_buses = nx.shortest_path(temp_graph, source=key, target=leaf_node)
                    if len(succ_buses) > 0:
                        for pred_bus in succ_buses:
                            if pred_bus == key:
                                continue
                            if self.pos_dict[pred_bus][0] != 0.0 and self.pos_dict[pred_bus][1] != 0.0:
                                new_x = self.pos_dict[pred_bus][0]
                                new_y = self.pos_dict[pred_bus][1]
                                self.G.node[key]["pos"] = [new_x, new_y]
                                break
        # Update pos dict with new coordinates
        self.pos_dict = nx.get_node_attributes(self.G, 'pos')

    def generate_nodes(self):
        self.nodes_list = []
        for b in self.all_bus_names:
            dss.Circuit.SetActiveBus(b)
            name = b.lower()
            position = []
            position.append(dss.Bus.X())
            position.append(dss.Bus.Y())
            self.G.add_node(name, pos=position)
            self.nodes_list.append(b)

    def generate_edges(self):
        '''
        All lines, switches, reclosers etc are modeled as lines, so calling lines takes care of all of them.
        However we also need to loop over transformers as they form the edge between primary and secondary nodes
        :return:
        '''
        dss.Lines.First()
        while True:
            from_bus = dss.Lines.Bus1().split('.')[0].lower()
            to_bus = dss.Lines.Bus2().split('.')[0].lower()
            phases = dss.Lines.Phases()
            length = dss.Lines.Length()
            name = dss.Lines.Name()
            if dss.Lines.Units() == 1:
                length = length * 1609.34
            elif dss.Lines.Units() == 2:
                length = length * 304.8
            elif dss.Lines.Units() == 3:
                length = length * 1000
            elif dss.Lines.Units() == 4:
                length = length
            elif dss.Lines.Units() == 5:
                length = length * 0.3048
            elif dss.Lines.Units() == 6:
                length = length * 0.0254
            elif dss.Lines.Units() == 7:
                length = length * 0.01
            self.G.add_edge(from_bus, to_bus, phases=phases, length=length, name=name)
            if not dss.Lines.Next() > 0:
                break

        dss.Transformers.First()
        while True:
            bus_names = dss.CktElement.BusNames()
            from_bus = bus_names[0].split('.')[0].lower()
            to_bus = bus_names[1].split('.')[0].lower()
            phases = dss.CktElement.NumPhases()
            length = 0.0
            name = dss.Transformers.Name()
            self.G.add_edge(from_bus, to_bus, phases=phases, length=length, name=name)
            if not dss.Transformers.Next() > 0:
                break

    def plot_feeder(self):
        plt.figure(figsize=(7, 7))
        ec = nx.draw_networkx_edges(self.G, pos=self.pos_dict, alpha=1.0, width=0.3)
        ldn = nx.draw_networkx_nodes(self.G, pos=self.pos_dict, nodelist=self.nodes_list, node_size=8,
                                     node_color='k', alpha=1)
        ld = nx.draw_networkx_nodes(self.G, pos=self.pos_dict, nodelist=self.nodes_list, node_size=6,
                                    node_color='yellow', alpha=0.7)

        nx.draw_networkx_labels(self.G, pos=self.pos_dict, node_size=1, font_size=15)
        plt.title("Feeder with all customers having DPV systems")
        plt.axis("off")
        plt.show()

    def check_voltage_violations_multi_tps(self):
        # TODO: This objective currently gives more weightage if same node has violations at more than 1 time point
        num_nodes_counter = 0
        severity_counter = 0
        self.max_V_viol = 0
        self.min_V_viol = 2
        self.buses_with_violations = []
        self.buses_with_violations_pos = {}
        self.nodal_violations_dict = {}
        for tp_cnt in range(len(self.Settings["tps_to_test"])):
            # First two tps are for disabled PV case
            if tp_cnt == 0 or tp_cnt == 1:
                dss.run_command("BatchEdit PVSystem..* Enabled=False")
                dss.run_command("set LoadMult = {LM}".format(LM=self.Settings["tps_to_test"][tp_cnt]))
                self.dssSolver.Solve()
                if not dss.Solution.Converged():
                    print("OpenDSS solution did not converge, quitting...")
                    print("Here2")
                    quit()
            if tp_cnt == 2 or tp_cnt == 3:
                dss.run_command("BatchEdit PVSystem..* Enabled=True")
                dss.run_command("set LoadMult = {LM}".format(LM=self.Settings["tps_to_test"][tp_cnt]))
                self.dssSolver.Solve()
                if not dss.Solution.Converged():
                    print("OpenDSS solution did not converge, quitting...")
                    print("Here3")
                    quit()
            for b in self.all_bus_names:
                dss.Circuit.SetActiveBus(b)
                bus_v = dss.Bus.puVmagAngle()[::2]
                # Select that bus voltage of the three phases which is outside bounds the most,
                #  else if everything is within bounds use nominal pu voltage.
                maxv_dev = 0
                minv_dev = 0
                if max(bus_v) > self.max_V_viol:
                    self.max_V_viol = max(bus_v)
                    self.busvmax = b
                if min(bus_v) < self.min_V_viol:
                    self.min_V_viol = min(bus_v)
                if max(bus_v) > self.Settings["Range B upper"]:
                    maxv = max(bus_v)
                    maxv_dev = maxv - self.Settings["Range B upper"]
                if min(bus_v) < self.Settings["Range B lower"]:
                    minv = min(bus_v)
                    minv_dev = self.Settings["Range B upper"] - minv
                if maxv_dev > minv_dev:
                    v_used = maxv
                    num_nodes_counter += 1
                    severity_counter += maxv_dev
                    if b.lower() not in self.buses_with_violations:
                        self.buses_with_violations.append(b.lower())
                        self.buses_with_violations_pos[b.lower()] = self.pos_dict[b.lower()]
                elif minv_dev > maxv_dev:
                    v_used = minv
                    num_nodes_counter += 1
                    severity_counter += minv_dev
                    if b.lower() not in self.buses_with_violations:
                        self.buses_with_violations.append(b.lower())
                        self.buses_with_violations_pos[b.lower()] = self.pos_dict[b.lower()]
                else:
                    v_used = self.Settings["nominal pu voltage"]
                if b not in self.nodal_violations_dict:
                    self.nodal_violations_dict[b.lower()] = [v_used]
                elif b in self.nodal_violations_dict:
                    self.nodal_violations_dict[b.lower()].append(v_used)
        self.severity_indices = [num_nodes_counter, severity_counter, num_nodes_counter * severity_counter]
        return

    def plot_violations(self):
        plt.figure(figsize=(8, 7))
        plt.clf()
        numV = len(self.buses_with_violations)
        plt.title("Number of buses in the feeder with voltage violations: {}".format(numV))
        ec = nx.draw_networkx_edges(self.G, pos=self.pos_dict, alpha=1.0, width=0.3)
        ld = nx.draw_networkx_nodes(self.G, pos=self.pos_dict, nodelist=self.nodes_list, node_size=2, node_color='b')
        # Show buses with violations
        if len(self.buses_with_violations) > 0:
            m = nx.draw_networkx_nodes(self.G, pos=self.buses_with_violations_pos,
                                       nodelist=self.buses_with_violations, node_size=10, node_color='r')
        plt.axis("off")
        plt.show()

    def get_capacitor_state(self):
        # TODO: How to figure out whether cap banks are 3 phase, 2 phase or 1 phase. 1 phase caps will have LN voltage
        self.cap_correct_PTratios = {}
        self.cap_initial_settings = {}
        dss.Capacitors.First()
        while True:
            name = dss.Capacitors.Name()
            # Get original cap bank control settings
            if dss.CapControls.Count() > 0:
                dss.CapControls.First()
                while True:
                    cap_name = dss.CapControls.Capacitor()
                    cap_type = dss.Properties.Value("type")
                    if cap_name == name and cap_type.lower().startswith("volt"):
                        self.cap_initial_settings[name] = [dss.CapControls.ONSetting(), dss.CapControls.OFFSetting()]
                    if not dss.CapControls.Next() > 0:
                        break
            dss.Circuit.SetActiveElement("Capacitor." + name)
            cap_bus = dss.CktElement.BusNames()[0].split(".")[0]
            dss.Circuit.SetActiveBus(cap_bus)
            cap_kv = float(dss.Bus.kVBase())
            dss.Circuit.SetActiveElement("Capacitor." + name)
            PT_ratio = (cap_kv * 1000) / (self.Settings["nominal_voltage"])
            self.cap_correct_PTratios[name] = PT_ratio
            if not dss.Capacitors.Next() > 0:
                break

    def correct_cap_bank_settings(self):
        # TODO: Add a function to sweep through possible capacitor bank settings
        caps_with_control = []
        cap_on_settings_check = {}
        # Correct settings of those cap banks for which cap control object is available
        if dss.CapControls.Count() > 0:
            dss.CapControls.First()
            while True:
                name = dss.CapControls.Name()
                cap_name = dss.CapControls.Capacitor()
                caps_with_control.append(cap_name)
                orig_sett = ''
                if dss.Properties.Value("type").lower() == "voltage":
                    orig_sett = " !original"
                control_command = "Edit CapControl.{cc} PTRatio={pt} Type={tp} ONsetting={o} OFFsetting={of}" \
                                  " PTphase={ph} Delay={d} DelayOFF={dof} DeadTime={dt} enabled=True".format(
                    cc=name,
                    pt=self.cap_correct_PTratios[cap_name],
                    tp=self.cap_control,
                    o=self.capON,
                    of=self.capOFF,
                    ph=self.PTphase,
                    d=self.capONdelay,
                    dof=self.capOFFdelay,
                    dt=self.capdeadtime
                )
                control_command = control_command + orig_sett
                cap_on_settings_check[cap_name] = self.capON
                dss.run_command(control_command)
                self.dssSolver.Solve()
                self.write_dss_file(control_command)
                if not dss.CapControls.Next() > 0:
                    break
        # if there are caps without cap control add a cap control
        if dss.Capacitors.Count() > len(caps_with_control):
            dss.Capacitors.First()
            while True:
                cap_name = dss.Capacitors.Name()
                if cap_name not in caps_with_control:
                    cap_ctrl_name = "capctrl" + cap_name
                    cap_bus = dss.CktElement.BusNames()[0].split(".")[0]
                    # Get line to be controlled
                    dss.Lines.First()
                    while True:
                        Line_name = dss.Lines.Name()
                        bus1 = dss.Lines.Bus1().split(".")[0]
                        if bus1 == cap_bus:
                            break
                        if not dss.Lines.Next() > 0:
                            break
                    control_command = "New CapControl.{cc} element=Line.{el} terminal={trm} capacitor={cbank} PTRatio={pt} Type={tp}" \
                                      " ONsetting={o} OFFsetting={of} PTphase={ph} Delay={d} DelayOFF={dof} DeadTime={dt} enabled=True".format(
                        cc=cap_ctrl_name,
                        el=Line_name,
                        trm=self.terminal,
                        cbank=cap_name,
                        pt=self.cap_correct_PTratios[cap_name],
                        tp=self.cap_control,
                        o=self.capON,
                        of=self.capOFF,
                        ph=self.PTphase,
                        d=self.capONdelay,
                        dof=self.capOFFdelay,
                        dt=self.capdeadtime
                    )
                    if len(self.cap_initial_settings) > 0 and cap_ctrl_name not in self.cap_initial_settings:
                        self.cap_initial_settings[cap_name] = [self.capON, self.capOFF]
                    dss.run_command(control_command)
                    cap_on_settings_check[cap_name] = self.capON
                    self.dssSolver.Solve()
                    self.write_dss_file(control_command)
                dss.Circuit.SetActiveElement("Capacitor." + cap_name)
                if not dss.Capacitors.Next() > 0:
                    break

        self.dssSolver.Solve()

        # Check whether settings have been applied or not
        if dss.CapControls.Count() > 0:
            dss.CapControls.First()
            while True:
                cap_on = dss.CapControls.ONSetting()
                name = dss.CapControls.Name()
                if abs(cap_on - cap_on_settings_check[cap_name]) > 0.1:
                    print("Settings for cap bank {} not implemented".format(cap_name))
                if not dss.CapControls.Next() > 0:
                    break

        # self.get_nodal_violations()
        self.check_voltage_violations_multi_tps()

    def get_viols_with_initial_cap_settings(self):
        if len(self.cap_initial_settings) > 0:
            for key, vals in self.cap_initial_settings.items():
                dss.CapControls.First()
                while True:
                    cap_name = dss.CapControls.Capacitor()
                    if cap_name == key:
                        dss.CapControls.ONSetting(vals[0])
                        dss.CapControls.OFFSetting(vals[1])
                    if not dss.CapControls.Next() > 0:
                        break
            self.check_voltage_violations_multi_tps()
            key = "original"
            self.cap_sweep_res_dict[key] = self.severity_indices[2]

    def cap_settings_sweep(self):
        # This function increases differences between cap ON and OFF voltages in user defined increments,
        #  default 1 volt, until upper and lower bounds are reached.
        self.cap_sweep_res_dict = {}
        self.get_viols_with_initial_cap_settings()
        self.cap_on_setting = self.capON
        self.cap_off_setting = self.capOFF
        self.cap_control_gap = self.Settings["Cap sweep voltage gap"]
        while self.cap_on_setting > self.Settings["Range B lower"] * self.Settings[
            "nominal_voltage"] or self.cap_off_setting < self.Settings["Range B upper"] * self.Settings[
            "nominal_voltage"]:
            # Apply cap ON and OFF settings and determine their impact
            key = "{}_{}".format(self.cap_on_setting, self.cap_off_setting)
            dss.CapControls.First()
            while True:
                cc_name = dss.CapControls.Name()
                dss.run_command("Edit CapControl.{cc} ONsetting={o} OFFsetting={of}".format(
                    cc=cc_name,
                    o=self.cap_on_setting,
                    of=self.cap_off_setting
                ))
                self.dssSolver.Solve()
                if not dss.CapControls.Next() > 0:
                    break
            self.check_voltage_violations_multi_tps()
            self.cap_sweep_res_dict[key] = self.severity_indices[2]
            if (self.cap_on_setting - self.cap_control_gap / 2) <= self.Settings["Range B lower"] * self.Settings[
                "nominal_voltage"]:
                self.cap_on_setting = self.Settings["Range B lower"] * self.Settings["nominal_voltage"]
            else:
                self.cap_on_setting = self.cap_on_setting - self.cap_control_gap / 2
            if (self.cap_off_setting + self.cap_control_gap / 2) >= self.Settings["Range B upper"] * self.Settings[
                "nominal_voltage"]:
                self.cap_off_setting = self.Settings["Range B upper"] * self.Settings["nominal_voltage"]
            else:
                self.cap_off_setting = self.cap_off_setting + self.cap_control_gap / 2
        self.apply_best_capsetting()

    def apply_orig_cap_setting(self):
        for key, vals in self.cap_initial_settings.items():
            dss.CapControls.First()
            while True:
                cap_name = dss.CapControls.Capacitor()
                if cap_name == key:
                    command_string = "Edit CapControl.{ccn} ONsetting={o} OFFsetting={of} !original".format(
                        ccn=dss.CapControls.Name(),
                        o=vals[0],
                        of=vals[1]
                    )
                    dss.run_command(command_string)
                    self.write_dss_file(command_string)
                if not dss.CapControls.Next() > 0:
                    break
        self.check_voltage_violations_multi_tps()

    def apply_best_capsetting(self):
        best_setting = ''
        # Start with assumption that each node has a violation at all time points and each violation if outside bounds
        #  by upper voltage limit - basically the maximum possible severity
        min_severity = pow(len(self.all_bus_names), 2) * len(self.Settings["tps_to_test"]) * self.Settings[
            "Range B upper"]
        for key, val in self.cap_sweep_res_dict.items():
            if val < min_severity:
                min_severity = val
                best_setting = key
        # Apply best settings which give minimum severity index
        if best_setting == "original":
            self.apply_orig_cap_setting()
        else:
            best_on_setting = best_setting.split("_")[0]
            best_off_setting = best_setting.split("_")[1]
            dss.CapControls.First()
            while True:
                cc_name = dss.CapControls.Name()
                command_string = ("Edit CapControl.{cc} ONsetting={o} OFFsetting={of}".format(
                    cc=cc_name,
                    o=best_on_setting,
                    of=best_off_setting
                ))
                dss.run_command(command_string)
                self.write_dss_file(command_string)
                self.dssSolver.Solve()
                if not dss.CapControls.Next() > 0:
                    break
        self.check_voltage_violations_multi_tps()

    def write_dss_file(self, device_command):
        self.dss_upgrades.append(device_command + "\n")
        return

    def write_upgrades_to_file(self):
        with open(os.path.join(self.Settings["Outputs"], "Voltage_upgrades.dss"), "w") as datafile:
            for line in self.dss_upgrades:
                datafile.write(line)
        return

    def reg_controls_sweep(self):
        self.vsps = []
        v = self.Settings["Range B lower"] * self.Settings["nominal_voltage"]
        while v < self.Settings["Range B upper"] * self.Settings["nominal_voltage"]:
            self.vsps.append(v)
            v += self.Settings["reg v delta"]
        for reg_sp in self.vsps:
            for bandw in self.Settings["reg control bands"]:
                dss.RegControls.First()
                while True:
                    regctrl_name = dss.RegControls.Name()
                    xfmr = dss.RegControls.Transformer()
                    dss.Circuit.SetActiveElement("Transformer.{}".format(xfmr))
                    xfmr_buses = dss.CktElement.BusNames()
                    xfmr_b1 = xfmr_buses[0].split(".")[0]
                    xfmr_b2 = xfmr_buses[1].split(".")[0]
                    # Skipping over substation LTC if it exists
                    # for n, d in self.G.in_degree().items():
                    #     if d == 0:
                    #         sourcebus = n
                    sourcebus = self.source
                    if xfmr_b1.lower() == sourcebus.lower() or xfmr_b2.lower() == sourcebus.lower():
                        dss.Circuit.SetActiveElement("Regcontrol.{}".format(regctrl_name))
                        if not dss.RegControls.Next() > 0:
                            break
                        continue
                    dss.Circuit.SetActiveElement("Regcontrol.{}".format(regctrl_name))

                    command_string = "Edit Regcontrol.{rn} vreg={sp} band={b}".format(
                        rn=regctrl_name,
                        sp=reg_sp,
                        b=bandw
                    )
                    dss.run_command(command_string)
                    self.dssSolver.Solve()
                    if not dss.RegControls.Next() > 0:
                        break
                self.check_voltage_violations_multi_tps()
                self.reg_sweep_viols["{}_{}".format(str(reg_sp), str(bandw))] = self.severity_indices[2]
        self.apply_best_regsetting()

    def apply_best_regsetting(self):
        # TODO: Remove substation LTC from the settings sweep
        best_setting = ''
        # Start with assumption that each node has a violation at all time points and each violation if outside bounds
        #  by upper voltage limit - basically the maximum possible severity
        min_severity = pow(len(self.all_bus_names), 2) * len(self.Settings["tps_to_test"]) * self.Settings[
            "Range B upper"]
        for key, val in self.reg_sweep_viols.items():
            if val < min_severity:
                min_severity = val
                best_setting = key
        if best_setting == "original":
            self.apply_orig_reg_setting()
        else:
            self.v_sp = best_setting.split("_")[0]
            self.reg_band = best_setting.split("_")[1]
            dss.RegControls.First()
            while True:
                reg_ctrl_nm = dss.RegControls.Name()
                xfmr = dss.RegControls.Transformer()
                dss.Circuit.SetActiveElement("Transformer.{}".format(xfmr))
                xfmr_buses = dss.CktElement.BusNames()
                xfmr_b1 = xfmr_buses[0].split(".")[0]
                xfmr_b2 = xfmr_buses[1].split(".")[0]
                dss.Circuit.SetActiveElement("Regcontrol.{}".format(reg_ctrl_nm))
                # Skipping over substation LTC if it exists
                # for n, d in self.G.in_degree().items():
                #     if d == 0:
                #         sourcebus = n
                sourcebus = self.source
                if xfmr_b1.lower() == sourcebus.lower() or xfmr_b2.lower() == sourcebus.lower():
                    dss.Circuit.SetActiveElement("Regcontrol.{}".format(reg_ctrl_nm))
                    if not dss.RegControls.Next() > 0:
                        break
                    continue
                command_string = "Edit RegControl.{rn} vreg={sp} band={b}".format(
                    rn=reg_ctrl_nm,
                    sp=self.v_sp,
                    b=self.reg_band,
                )
                dss.run_command(command_string)
                self.dssSolver.Solve()
                if self.write_flag == 1:
                    self.write_dss_file(command_string)
                if not dss.RegControls.Next() > 0:
                    break

    def apply_orig_reg_setting(self):
        for key, vals in self.initial_regctrls_settings.items():
            dss.Circuit.SetActiveElement("RegControl.{}".format(key))
            command_string = "Edit RegControl.{rn} vreg={sp} band={b} !original".format(
                rn=key,
                sp=vals[0],
                b=vals[1],
            )
            dss.run_command(command_string)
            self.dssSolver.Solve()
            if self.write_flag == 1:
                self.write_dss_file(command_string)

    def add_substation_LTC(self):
        # This function identifies whether or not a substation LTC exists - if not adds one along with a new sub xfmr
        # if required -  if one exists corrects its settings
        # Identify source bus
        # It seems that networkx in a directed graph only counts edges incident on a node towards degree.
        #  This is why source bus is the only bus which has a degree of zero
        # for n, d in self.G.in_degree().items():
        #     if d == 0:
        #         self.source = n

        # Identify whether a transformer is connected to this bus or not
        self.subxfmr = ''
        dss.Transformers.First()
        while True:
            bus_names = dss.CktElement.BusNames()
            from_bus = bus_names[0].split('.')[0].lower()
            to_bus = bus_names[1].split('.')[0].lower()
            if from_bus == self.source or to_bus == self.source:
                self.subxfmr = dss.Transformers.Name()
                self.sub_LTC_bus = to_bus
                break
            if not dss.Transformers.Next() > 0:
                break

        if self.subxfmr == '':
            # Add new transformer if no transformer is connected to source bus
            self.add_new_xfmr(self.source)
            self.write_flag = 1
            self.add_new_regctrl(self.source)
        elif self.subxfmr != '':
            self.write_flag = 1
            self.add_new_regctrl(self.sub_LTC_bus)
        self.check_voltage_violations_multi_tps()
        return

    def add_new_xfmr(self, node):
        # If substation does not have a transformer add a transformer at the source bus so a new reg
        # control object may be created -  after the transformer and reg control have been added the feeder would have
        #  to be re compiled as system admittance matrix has changed
        # Find line to which this node is connected to
        # node = node.lower()
        degree = 1
        if node.lower() == self.source.lower():
            degree = 0

        dss.Lines.First()
        while True:
            # For sub LTC
            if degree == 0:
                if dss.Lines.Bus1().split(".")[0] == node:
                    bus1 = dss.Lines.Bus1()
                    bus2 = dss.Lines.Bus2()
                    new_node = "Regctrl_" + bus2
                    xfmr_name = "New_xfmr_" + node
                    line_name = dss.Lines.Name()
                    phases = dss.Lines.Phases()
                    amps = dss.CktElement.NormalAmps()
                    dss.Circuit.SetActiveBus(bus1)
                    x = dss.Bus.X()
                    y = dss.Bus.Y()
                    dss.Circuit.SetActiveBus(bus2)
                    kV_node = dss.Bus.kVBase()
                    if phases > 1:
                        kV_DT = kV_node * 1.732
                        kVA = int(kV_DT * amps * 1.1)  # 10% over sized transformer - ideally we
                        # would use an auto transformer which would need a much smaller kVA rating
                        command_string = "New Transformer.{xfn} phases={p} windings=2 buses=({b1},{b2}) conns=({cntp},{cntp})" \
                                         " kvs=({kv},{kv}) kvas=({kva},{kva}) xhl=0.001 wdg=1 %r=0.001 wdg=2 %r=0.001" \
                                         " Maxtap=1.1 Mintap=0.9 enabled=True".format(
                            xfn=xfmr_name,
                            p=phases,
                            b1=bus1,
                            b2=new_node,
                            cntp=self.reg_conn,
                            kv=kV_DT,
                            kva=kVA
                        )
                    elif phases == 1:
                        kVA = int(kV_node * amps * 1.1)  # 10% over sized transformer - ideally we
                        # would use an auto transformer which would need a much smaller kVA rating
                        # make bus1 of line as reg ctrl node
                        command_string = "New Transformer.{xfn} phases={p} windings=2 buses=({b1},{b2}) conns=({cntp},{cntp})" \
                                         " kvs=({kv},{kv}) kvas=({kva},{kva}) xhl=0.001 wdg=1 %r=0.001 wdg=2 %r=0.001" \
                                         " Maxtap=1.1 Mintap=0.9 enabled=True".format(
                            xfn=xfmr_name,
                            p=phases,
                            b1=bus1,
                            b2=new_node,
                            cntp=self.reg_conn,
                            kv=kV_node,
                            kva=kVA
                        )
                    control_command = "Edit Line.{} bus1={}".format(line_name, new_node)
                    dss.run_command(control_command)
                    if self.write_flag == 1:
                        self.write_dss_file(control_command)
                    dss.run_command(command_string)
                    if self.write_flag == 1:
                        self.write_dss_file(command_string)
                    # Update system admittance matrix
                    dss.run_command("CalcVoltageBases")
                    dss.Circuit.SetActiveBus(new_node)
                    dss.Bus.X(x)
                    dss.Bus.Y(y)
                    if self.write_flag == 1:
                        self.write_dss_file("//{},{},{}".format(new_node.split(".")[0], x, y))
                    self.dssSolver.Solve()
                    self.generate_nx_representation()
                    dss.Circuit.SetActiveElement("Line." + line_name)
                    break
            # For regulator
            elif degree > 0:
                if dss.Lines.Bus2().split(".")[0] == node:
                    bus1 = dss.Lines.Bus1()
                    bus2 = dss.Lines.Bus2()
                    new_node = "Regctrl_" + bus2
                    xfmr_name = "New_xfmr_" + node
                    line_name = dss.Lines.Name()
                    phases = dss.Lines.Phases()
                    amps = dss.CktElement.NormalAmps()
                    dss.Circuit.SetActiveBus(bus2)
                    x = dss.Bus.X()
                    y = dss.Bus.Y()
                    kV_node = dss.Bus.kVBase()
                    if phases > 1:
                        kV_DT = kV_node * 1.732
                        kVA = int(kV_DT * amps * 1.1)  # 10% over sized transformer - ideally we
                        # would use an auto transformer which would need a much smaller kVA rating

                        command_string = "New Transformer.{xfn} phases={p} windings=2 buses=({b1},{b2}) conns=(wye,wye)" \
                                         " kvs=({kv},{kv}) kvas=({kva},{kva}) xhl=0.001 wdg=1 %r=0.001 wdg=2 %r=0.001" \
                                         " Maxtap=1.1 Mintap=0.9 enabled=True".format(
                            xfn=xfmr_name,
                            p=phases,
                            b1=new_node,
                            b2=bus2,
                            kv=kV_DT,
                            kva=kVA
                        )
                    elif phases == 1:
                        kVA = int(kV_node * amps * 1.1)  # 10% over sized transformer - ideally we
                        # would use an auto transformer which would need a much smaller kVA rating
                        command_string = "New Transformer.{xfn} phases={p} windings=2 buses=({b1},{b2}) conns=(wye,wye)" \
                                         " kvs=({kv},{kv}) kvas=({kva},{kva}) xhl=0.001 wdg=1 %r=0.001 wdg=2 %r=0.001" \
                                         " Maxtap=1.1 Mintap=0.9 enabled=True".format(
                            xfn=xfmr_name,
                            p=phases,
                            b1=new_node,
                            b2=bus2,
                            kv=kV_node,
                            kva=kVA
                        )
                    control_command = "Edit Line.{} bus2={}".format(line_name, new_node)
                    dss.run_command(control_command)
                    if self.write_flag == 1:
                        self.write_dss_file(control_command)
                    dss.run_command(command_string)
                    if self.write_flag == 1:
                        self.write_dss_file(command_string)
                    # Update system admittance matrix
                    dss.run_command("CalcVoltageBases")
                    dss.Circuit.SetActiveBus(new_node)
                    dss.Bus.X(x)
                    dss.Bus.Y(y)
                    if self.write_flag == 1:
                        self.write_dss_file("//{},{},{}".format(new_node.split(".")[0], x, y))
                    self.dssSolver.Solve()
                    self.generate_nx_representation()
                    dss.Circuit.SetActiveElement("Line." + line_name)
                    break
            if not dss.Lines.Next() > 0:
                break

        return

    def add_new_regctrl(self, node):
        # Identify whether or not a reg contrl exists at the transformer connected to this bus - a transformer should
        # definitely exist by now. If it does correct its settings else add a new reg ctrl with correct settings
        # If transformer exists check if it already has a reg control object
        if dss.Transformers.Count() > 0:
            dss.Transformers.First()
            while True:
                # Identify transformer connected to this node
                bus_prim = dss.CktElement.BusNames()[0].split(".")[0]
                bus_sec = dss.CktElement.BusNames()[1].split(".")[0]
                if bus_prim == node or bus_sec == node:
                    xfmr_name = dss.Transformers.Name()
                    phases = dss.CktElement.NumPhases()
                    dss.Circuit.SetActiveBus(node)
                    # node info is only used to get correct kv values
                    kV = dss.Bus.kVBase()
                    winding = self.LTC_wdg
                    vreg = self.LTC_setpoint
                    reg_delay = self.LTC_delay
                    deadband = self.LTC_band
                    pt_ratio = kV * 1000 / (self.Settings["nominal_voltage"])

                    xfmr_regctrl = ''

                    # Identify whether a reg control exists on this transformer
                    if dss.RegControls.Count() > 0:
                        dss.RegControls.First()
                        while True:
                            xfmr_name_reg = dss.RegControls.Transformer()
                            if xfmr_name_reg == xfmr_name:
                                # if reg control already exists correct its settings
                                xfmr_regctrl = dss.RegControls.Name()
                                command_string = "Edit RegControl.{rn} transformer={tn} winding={w} vreg={sp} ptratio={rt} band={b} " \
                                                 "enabled=true delay={d}".format(
                                    rn=xfmr_regctrl,
                                    tn=xfmr_name,
                                    w=winding,
                                    sp=vreg,
                                    rt=pt_ratio,
                                    b=deadband,
                                    d=reg_delay
                                )
                                dss.run_command(command_string)
                                self.dssSolver.Solve()
                                dss.run_command("Calcvoltagebases")
                                # add this to a dss_upgrades.dss file
                                if self.write_flag == 1:
                                    self.write_dss_file(command_string)
                                # check for violations
                                # self.get_nodal_violations()
                                self.check_voltage_violations_multi_tps()
                                break
                            if not dss.RegControls.Next() > 0:
                                break
                    if xfmr_regctrl == '':
                        # if reg control does not exist on the transformer add one
                        xfmr_regctrl = "New_regctrl_" + node
                        command_string = "New RegControl.{rn} transformer={tn} winding={w} vreg={sp} ptratio={rt} band={b} " \
                                         "enabled=true delay={d}".format(
                            rn=xfmr_regctrl,
                            tn=xfmr_name,
                            w=winding,
                            sp=vreg,
                            rt=pt_ratio,
                            b=deadband,
                            d=reg_delay
                        )
                        dss.run_command(command_string)
                        self.dssSolver.Solve()
                        dss.run_command("CalcVoltageBases")
                        # add this to a dss_upgrades.dss file
                        if self.write_flag == 1:
                            self.write_dss_file(command_string)
                        # check for violations
                        # self.get_nodal_violations()
                        self.check_voltage_violations_multi_tps()
                        break
                    dss.Circuit.SetActiveElement("Transformer." + xfmr_name)
                if not dss.Transformers.Next() > 0:
                    break
        return

    def LTC_controls_sweep(self):
        self.vsps = []
        v = self.Settings["Range B lower"] * self.Settings["nominal_voltage"]
        while v < self.Settings["Range B upper"] * self.Settings["nominal_voltage"]:
            self.vsps.append(v)
            v += self.Settings["reg v delta"]
        for reg_sp in self.vsps:
            for bandw in self.Settings["reg control bands"]:
                dss.RegControls.First()
                while True:
                    regctrl_name = dss.RegControls.Name()
                    xfmr = dss.RegControls.Transformer()
                    dss.Circuit.SetActiveElement("Transformer.{}".format(xfmr))
                    xfmr_buses = dss.CktElement.BusNames()
                    xfmr_b1 = xfmr_buses[0].split(".")[0]
                    xfmr_b2 = xfmr_buses[1].split(".")[0]
                    # Skipping over substation LTC if it exists
                    # for n, d in self.G.in_degree().items():
                    #     if d == 0:
                    #         sourcebus = n
                    sourcebus = self.source
                    if xfmr_b1.lower() == sourcebus.lower() or xfmr_b2.lower() == sourcebus.lower():
                        dss.Circuit.SetActiveElement("Regcontrol.{}".format(regctrl_name))
                        command_string = "Edit Regcontrol.{rn} vreg={sp} band={b}".format(
                            rn=regctrl_name,
                            sp=reg_sp,
                            b=bandw
                        )
                        dss.run_command(command_string)
                        self.dssSolver.Solve()
                    dss.Circuit.SetActiveElement("Regcontrol.{}".format(regctrl_name))
                    if not dss.RegControls.Next() > 0:
                        break
                self.check_voltage_violations_multi_tps()
                self.subLTC_sweep_viols["{}_{}".format(str(reg_sp), str(bandw))] = self.severity_indices[2]
        self.apply_best_LTCsetting()

    def apply_best_LTCsetting(self):
        # TODO: Remove substation LTC from the settings sweep
        best_setting = ''
        # Start with assumption that each node has a violation at all time points and each violation if outside bounds
        #  by upper voltage limit - basically the maximum possible severity
        min_severity = pow(len(self.all_bus_names), 2) * len(self.Settings["tps_to_test"]) * self.Settings[
            "Range B upper"]
        for key, val in self.subLTC_sweep_viols.items():
            if val < min_severity:
                min_severity = val
                best_setting = key
        if best_setting == "original":
            self.apply_orig_LTC_setting()
        else:
            v_sp = best_setting.split("_")[0]
            reg_band = best_setting.split("_")[1]
            dss.RegControls.First()
            while True:
                reg_ctrl_nm = dss.RegControls.Name()
                xfmr = dss.RegControls.Transformer()
                dss.Circuit.SetActiveElement("Transformer.{}".format(xfmr))
                xfmr_buses = dss.CktElement.BusNames()
                xfmr_b1 = xfmr_buses[0].split(".")[0]
                xfmr_b2 = xfmr_buses[1].split(".")[0]
                # # Skipping over substation LTC if it exists
                # for n, d in self.G.in_degree().items():
                #     if d == 0:
                #         sourcebus = n
                sourcebus = self.source
                if xfmr_b1.lower() == sourcebus.lower() or xfmr_b2.lower() == sourcebus.lower():
                    dss.Circuit.SetActiveElement("Regcontrol.{}".format(reg_ctrl_nm))

                    command_string = "Edit RegControl.{rn} vreg={sp} band={b}".format(
                        rn=reg_ctrl_nm,
                        sp=v_sp,
                        b=reg_band,
                    )
                    dss.run_command(command_string)
                    self.dssSolver.Solve()
                    self.write_dss_file(command_string)
                dss.Circuit.SetActiveElement("Regcontrol.{}".format(reg_ctrl_nm))
                if not dss.RegControls.Next() > 0:
                    break
        self.check_voltage_violations_multi_tps()

    def apply_orig_LTC_setting(self):
        for key, vals in self.initial_subLTC_settings.items():
            dss.Circuit.SetActiveElement("RegControl.{}".format(key))
            command_string = "Edit RegControl.{rn} vreg={sp} band={b} !original".format(
                rn=key,
                sp=vals[0],
                b=vals[1],
            )
            dss.run_command(command_string)
            self.dssSolver.Solve()
            self.write_dss_file(command_string)

    def get_shortest_path(self):
        new_graph = self.G.to_undirected()
        precal_paths = []
        self.UT_paths_dict = {}
        # Get upper triangular distance matrix - reduces computational time by half
        for bus1 in self.buses_with_violations:
            self.UT_paths_dict[bus1] = []
            for bus_n in self.buses_with_violations:
                if bus_n == bus1:
                    path_length = 0.0
                elif bus_n in precal_paths:
                    continue
                else:
                    path = nx.shortest_path(new_graph, source=bus1, target=bus_n)
                    path_length = 0.0
                    for nodes_count in range(len(path) - 1):
                        path_length += float(new_graph[path[nodes_count + 1]][path[nodes_count]]['length'])
                self.UT_paths_dict[bus1].append(round(path_length, 3))
            precal_paths.append(bus1)

    def get_full_distance_dict(self):
        self.square_array = []
        self.square_dict = {}
        self.cluster_nodes_list = []
        temp_nodes_list = []
        ll = []
        max_length = 0
        for key, values in self.UT_paths_dict.items():
            self.cluster_nodes_list.append(key)
            if len(values) > max_length:
                max_length = len(values)
        # Create a square dict with zeros for lower triangle values
        for key, values in self.UT_paths_dict.items():
            temp_nodes_list.append(key)
            temp_list = []
            if len(values) < max_length:
                new_items_req = max_length - len(values)
                for items_cnt in range(0, new_items_req, 1):
                    temp_list.append(0.0)
            for item in values:
                temp_list.append(float(item))
            self.square_dict[key] = temp_list
        # Replace lower triangle zeros with upper triangle values
        key_count = 0
        for key, values in self.UT_paths_dict.items():
            for items_count in range(len(values)):
                self.square_dict[temp_nodes_list[items_count]][key_count] = values[items_count]
            key_count += 1
            temp_nodes_list.remove(key)
        # from dict create a list of lists
        for key, values in self.square_dict.items():
            ll.append(values)
        # Create numpy array from list of lists
        self.square_array = np.array(ll)

    def plot_heatmap_distmatrix(self):
        plt.figure(figsize=(7, 7))
        ax = sns.heatmap(self.square_array, linewidth=0.5)
        plt.title("Distance matrix of nodes with violations")
        plt.show()

    def cluster_square_array(self):
        # Clustering the distance matrix into clusters equal to optimal clusters
        if self.Settings["create topology plots"]:
            self.plot_heatmap_distmatrix()
        for self.optimal_clusters in range(1, self.max_regs + 1, 1):
            self.no_reg_flag = 0
            self.clusters_dict = {}
            model = AgglomerativeClustering(n_clusters=self.optimal_clusters, affinity='euclidean', linkage='ward')
            model.fit(self.square_array)
            labels = model.labels_
            for lab in range(len(labels)):
                if labels[lab] not in self.clusters_dict:
                    self.clusters_dict[labels[lab]] = [self.cluster_nodes_list[lab]]
                else:
                    self.clusters_dict[labels[lab]].append(self.cluster_nodes_list[lab])
            self.identify_correct_reg_node()
            self.add_new_reg_common_nodes()
            if self.no_reg_flag == 1:
                continue
            self.write_flag = 0
            self.reg_controls_sweep()
            self.write_flag = 1
            self.check_voltage_violations_multi_tps()
            self.cluster_optimal_reg_nodes[self.optimal_clusters] = [self.severity_indices[2],
                                                                     [self.v_sp, self.reg_band], []]
            # Store all optimal nodes for a given number of clusters
            for key, vals in self.upstream_reg_node.items():
                self.cluster_optimal_reg_nodes[self.optimal_clusters][2].append(vals)
            if self.Settings["create topology plots"]:
                self.plot_created_clusters()
                self.plot_violations()
            print(self.max_V_viol, self.min_V_viol, self.severity_indices)
            self.disable_regctrl_current_cluster()
            if (len(self.buses_with_violations)) == 0:
                print("All nodal violations have been removed successfully.....quitting")
                break

    def disable_regctrl_current_cluster(self):
        disable_index = self.optimal_clusters
        if disable_index in self.cluster_optimal_reg_nodes:
            for node in self.cluster_optimal_reg_nodes[disable_index][2]:
                self.write_flag = 0
                self.disable_added_xfmr(node)
                self.write_flag = 1
                command_string = "Edit RegControl.{rn} enabled=False".format(
                    rn="New_regctrl_" + node
                )
                dss.run_command(command_string)
                # self.write_dss_file(command_string)
        return

    def disable_added_xfmr(self, node):
        # Unfortunately since OpenDSS disables by transformer by opening the circuit instead of creating a short circuit,
        # this function will remove the transformer by first disabling it, then it will connect the line properly to
        # remove the islands
        # Substation will always have a xfmr by this point so only regulator transformers have to be removed

        transformer_name = "New_xfmr_" + node

        dss.Transformers.First()
        while True:
            if dss.Transformers.Name().lower() == transformer_name.lower():
                prim_bus = dss.CktElement.BusNames()[0].split(".")[0]
                sec_bus = dss.CktElement.BusNames()[1]
                command_string = "Edit Transformer.{xfmr} enabled=False".format(xfmr=transformer_name)
                dss.run_command(command_string)
                if self.write_flag == 1:
                    self.write_dss_file(command_string)
                command_string = "Edit Transformer.{xfmr} buses=({b1},{b2})".format(
                    xfmr=transformer_name,
                    b1=dss.CktElement.BusNames()[0],
                    b2=dss.CktElement.BusNames()[0]
                )
                dss.run_command(command_string)
                if self.write_flag == 1:
                    self.write_dss_file(command_string)
                dss.Lines.First()
                while True:
                    if dss.Lines.Bus2().split(".")[0].lower() == prim_bus.lower():
                        command_string = "Edit Line.{ln} bus2={b}".format(
                            ln=dss.Lines.Name(),
                            b=sec_bus
                        )
                        dss.run_command(command_string)
                        if self.write_flag == 1:
                            self.write_dss_file(command_string)
                        # Update system admittance matrix
                        dss.run_command("CalcVoltageBases")
                        self.dssSolver.Solve()
                        self.generate_nx_representation()
                        break
                    if not dss.Lines.Next() > 0:
                        break
                break
            if not dss.Transformers.Next() > 0:
                break
        return

    def add_new_reg_common_nodes(self):
        # Identify whether a transformer exists at this node or not. If yes simply add a new reg control -
        # in fact calling the add_new_regctrl function will automatically check whether a reg control exists or not
        # -  so only thing to be ensured is that a transformer should exist - for next time when this function is called
        #  a new set of clusters will be passed
        self.upstream_reg_node = {}
        for cluster, common_nodes in self.upstream_nodes_dict.items():
            self.vdev_cluster_nodes = {}
            for node in common_nodes:
                continue_flag = 0
                # Here do not add a new reg control to source bus as it already has a LTC
                # for n, d in self.G.in_degree().items():
                #     if n == node and d==0:
                if node.lower() == self.source.lower():
                    continue_flag = 1
                if continue_flag == 1:
                    continue
                dss.Transformers.First()
                xfmr_flag = 0
                while True:
                    xfmr_name = dss.Transformers.Name()
                    # dss.Circuit.SetActiveElement("Transformer."+xfmr_name)
                    prim_bus = dss.CktElement.BusNames()[0].split(".")[0]
                    sec_bus = dss.CktElement.BusNames()[1].split(".")[0]
                    if node == sec_bus or node == prim_bus:
                        xfmr_flag = 1
                        break
                    if not dss.Transformers.Next() > 0:
                        break
                if xfmr_flag == 0:
                    self.write_flag = 0
                    self.add_new_xfmr(node)
                    # These are just trial settings and do not have to be written in the output file
                    self.add_new_regctrl(node)
                    self.write_flag = 1
                elif xfmr_flag == 1:
                    # The reason is that we are skipping over LTC node already, and all other other nodes with
                    # pre-existing xfmrs will be primary to secondary DTs which we do not want to control as regs are
                    # primarily in line and not on individual distribution transformers
                    continue
                self.vdev_cluster_nodes[node] = self.severity_indices[2]
                # self.get_nodes_withV(node)
                # Now disable the added regulator control and remove the added transformer
                if xfmr_flag == 0:
                    command_string = "Edit RegControl.{rn} enabled=No".format(
                        rn="New_regctrl_" + node
                    )
                    dss.run_command(command_string)
                    self.write_flag = 0
                    self.disable_added_xfmr(node)
                    self.write_flag = 1
            # For a given cluster identify the node which leads to minimum number of buses with violations
            min_severity = pow(len(self.all_bus_names), 2) * len(self.Settings["tps_to_test"]) * self.Settings[
                "Range B upper"]
            min_node = ''
            for key, value in self.vdev_cluster_nodes.items():
                if value <= min_severity:
                    min_severity = value
                    min_node = key
            print("Min node is:", min_node)
            # If no nodes is found break the loop and go to next number of clusters:
            if min_node == '':
                continue
            self.upstream_reg_node[cluster] = min_node
            # Since this is an optimal location add the transformer here - this transformer will stay as long as
            # self.optimal_clusters does not increment. If this parameter changes then all devices at nodes mentioned
            # in previous optimal cluster number in self.cluster_optimal_reg_nodes should be disabled
            self.write_flag = 0
            self.add_new_xfmr(min_node)
            self.write_flag = 1
            command_string = "Edit RegControl.{rn} enabled=True".format(
                rn="New_regctrl_" + min_node
            )
            dss.run_command(command_string)
            self.dssSolver.Solve()
            self.check_voltage_violations_multi_tps()
            # Even here we do not need to write out the setting as the only setting to be written would
            # self.write_dss_file(command_string)
        # if no reg control nodes are found then continue
        if len(self.upstream_reg_node) == 0:
            self.no_reg_flag = 1

        return

    def identify_correct_reg_node(self):
        # In this function the very first common upstream node and all upstream nodes for all members of the
        #  cluster are stored
        # TODO: include some type of optimization - such as look at multiple upstream nodes and place where sum of
        # TODO: downstream node voltage deviations is minimum as long as it doesn't overlap with other clusters
        # Currently it only identifies the common upstream nodes for all cluster nodes
        self.upstream_nodes_dict = {}

        temp_graph = self.G
        new_graph = temp_graph.to_undirected()
        for key, items in self.clusters_dict.items():
            paths_dict_cluster = {}
            common_nodes = []
            for buses in items:
                path = nx.shortest_path(new_graph, source=self.source, target=buses)
                paths_dict_cluster[buses] = path
            for common_bus in path:
                flag = 1
                for bus, paths in paths_dict_cluster.items():
                    if common_bus not in paths:
                        flag = 0
                        break
                if flag == 1:
                    common_nodes.append(common_bus)
            self.upstream_nodes_dict[key] = common_nodes
            # self.upstream_reg_node[key] = common_nodes[-1]
        return

    def plot_created_clusters(self):
        plt.figure(figsize=(7, 7))
        # Plots clusters and common paths from clusters to source
        plt.clf()
        self.pos_dict = nx.get_node_attributes(self.G, 'pos')
        ec = nx.draw_networkx_edges(self.G, pos=self.pos_dict, alpha=1.0, width=0.3)
        ld = nx.draw_networkx_nodes(self.G, pos=self.pos_dict, nodelist=self.cluster_nodes_list, node_size=2,
                                    node_color='b')
        # Show min V violations
        col = 0
        try:
            for key, values in self.clusters_dict.items():
                nodal_violations_pos = {}
                common_nodes_pos = {}
                reg_nodes_pos = {}
                for cluster_nodes in values:
                    nodal_violations_pos[cluster_nodes] = self.pos_dict[cluster_nodes]
                for common_nodes in self.upstream_nodes_dict[key]:
                    common_nodes_pos[common_nodes] = self.pos_dict[common_nodes]
                print(self.upstream_reg_node[key])
                reg_nodes_pos[self.upstream_reg_node[key]] = self.pos_dict[self.upstream_reg_node[key]]
                nx.draw_networkx_nodes(self.G, pos=nodal_violations_pos,
                                       nodelist=values, node_size=5, node_color='C{}'.format(col))
                nx.draw_networkx_nodes(self.G, pos=common_nodes_pos,
                                       nodelist=self.upstream_nodes_dict[key], node_size=5,
                                       node_color='C{}'.format(col), alpha=0.3)
                nx.draw_networkx_nodes(self.G, pos=reg_nodes_pos,
                                       nodelist=[self.upstream_reg_node[key]], node_size=25, node_color='r')
                col += 1
        except:
            pass
        plt.axis("off")
        plt.title("All buses with violations grouped in {} clusters".format(self.optimal_clusters))
        plt.show()

    def run(self, step, stepMax):
        """Induces and removes a fault as the simulation runs as per user defined settings. 
        """
        print('Running voltage upgrade post process')

        return step

