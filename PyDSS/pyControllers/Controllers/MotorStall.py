#Algebraic model for Type D motor - Residential air conditioner
'''
author: Kapil Duwadi
Version: 1.0
'''

from PyDSS.pyControllers.pyControllerAbstract import ControllerAbstract
import random
import math

class MotorStall(ControllerAbstract):
    """The controller locks a regulator in the event of reverse power flow. Subclass of the :class:`PyDSS.pyControllers.
    pyControllerAbstract.ControllerAbstract` abstract class.

                :param RegulatorObj: A :class:`PyDSS.dssElement.dssElement` object that wraps around an OpenDSS 'Regulator' element
                :type FaultObj: class:`PyDSS.dssElement.dssElement`
                :param Settings: A dictionary that defines the settings for the PvController.
                :type Settings: dict
                :param dssInstance: An :class:`opendssdirect` instance
                :type dssInstance: :class:`opendssdirect`
                :param ElmObjectList: Dictionary of all dssElement, dssBus and dssCircuit objects
                :type ElmObjectList: dict
                :param dssSolver: An instance of one of the classed defined in :mod:`PyDSS.SolveMode`.
                :type dssSolver: :mod:`PyDSS.SolveMode`
                :raises: AssertionError if 'RegulatorObj' is not a wrapped OpenDSS Regulator element

        """


    def __init__(self, MotorObj, Settings, dssInstance, ElmObjectList, dssSolver):
        super(MotorStall).__init__()
        _class, _name = MotorObj.GetInfo()
        self.name = "Controller-{}-{}".format(_class, _name)
        self.__ControlledElm = MotorObj
        self.__Settings = Settings
        self.__dssSolver = dssSolver

        self.__ControlledElm.SetParameter('model', 2)
        self.__ControlledElm.SetParameter('vminpu', 0.0)
        # self.kw = self.__ControlledElm.GetParameter('kw')
        # self.kvar = self.__ControlledElm.GetParameter('kvar')
        self.kw = self.__Settings['ratedKW']
        S = self.kw / self.__Settings['ratedPF']
        self.kvar = math.sqrt(S**2 - self.kw**2)
        self.__ControlledElm.SetParameter('kw', self.kw)
        self.__ControlledElm.SetParameter('kvar', self.kvar)
        self.stall_time_start = 0
        self.stall = False
        self.disconnected =False
        self.Tdisconnect_start = 0
        return

    def Update(self, Priority, Time, UpdateResults):
        if Priority == 0:
            Vbase = self.__ControlledElm.sBus[0].GetVariable('kVBase')
            Ve_mags = max(self.__ControlledElm.GetVariable('VoltagesMagAng')[::2])/ 120.0


            if Ve_mags < self.__Settings['Vstall'] and not self.stall:
                print(Ve_mags)
                self.__ControlledElm.SetParameter('kw', self.kw * self.__Settings['Pfault'] )
                self.__ControlledElm.SetParameter('kvar', self.kw * self.__Settings['Qfault'] )
                self.__ControlledElm.SetParameter('model', 1)
                self.stall = True
                self.stall_time_start = self.__dssSolver.GetTotalSeconds()
                return 0.1
            return 0
        if Priority == 1:
            if self.stall:
                self.stall_time = self.__dssSolver.GetTotalSeconds() - self.stall_time_start
                #print(self.stall_time)
                if self.stall_time > self.__Settings['Tprotection']:
                    self.stall = False
                    self.disconnected = True
                    self.__ControlledElm.SetParameter('kw', 0)
                    self.__ControlledElm.SetParameter('kvar', 0)
                    self.Tdisconnect_start = self.__dssSolver.GetTotalSeconds()
                return 0 #self.model_1(Priority)
            return 0
        if Priority == 2:
            if self.disconnected:
                time = self.__dssSolver.GetTotalSeconds() - self.Tdisconnect_start
                if time > self.__Settings['Treconnect']:
                    self.disconnected = False
                    self.__ControlledElm.SetParameter('kw', self.kw)
                    self.__ControlledElm.SetParameter('kvar', self.kvar)
                    self.__ControlledElm.SetParameter('model', 2)
                    self.__ControlledElm.SetParameter('vminpu', 0.0)

        return 0

