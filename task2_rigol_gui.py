import tkinter as tk # as tk เพื่อย่อชื่อจาก tkinter เป็น tk
from tkinter import ttk, messagebox 
# ttk คือชุด Widget ที่มีรูปลักษณ์ทันสมัยกว่า Widget แบบเดิมของ Tkinter
# messagebox ใช้สร้างกล่องข้อความที่เด้งขึ้นมา

import pyvisa # ไลบรารีที่ใช้ติดต่อกับเครื่องมือวัด เช่น RIGOL oscilloscope

# ผู้ใช้กดปุ่มใน GUI
#        ↓
# RigolGUI รับเหตุการณ์
#        ↓
# เรียก ScopeController
#        ↓
# PyVISA ส่งคำสั่งผ่าน USB
#        ↓
# RIGOL ทำงานตามคำสั่ง

class ScopeController:  # ติดต่อและควบคุมเครื่อง RIGOL
    """Control the RIGOL oscilloscope through PyVISA."""

    def __init__(self): #Constructor (ฟังก์ชันกำหนดค่าเริ่มต้น) ฟังก์ชันนี้จะทำงานอัตโนมัติเมื่อสร้าง Object
        self.resource_manager = None    #ยังไม่ได้สร้างตัวจัดการอุปกรณ์
        # ค้นหาและเปิดอุปกรณ์
        # Resource Manager (ตัวจัดการอุปกรณ์) คือ
        # ค้นหาอุปกรณ์เครื่องมือวัดที่ต่อกับคอมพิวเตอร์
        # แสดงรายการอุปกรณ์ที่พบ
        # เปิดการเชื่อมต่อกับอุปกรณ์ที่เลือก
        # จัดการช่องทางการสื่อสารของ PyVISA
        
        self.scope = None   # ยังไม่ได้เชื่อมต่อกับออสซิลโลสโคป
        # ใช้ส่งคำสั่งไปยังเครื่อง RIGOL (ใช้เก็บ การเชื่อมต่อกับ Oscilloscope (ออสซิลโลสโคป) เครื่องที่เปิดใช้งานแล้ว)

    def connect(self):
        """Connect to the first USB instrument found."""
        if self.scope is not None:
            raise RuntimeError("The oscilloscope is already connected.")    # ข้อผิดพลาดนี้จะถูกส่งไปยังส่วน except ใน GUI เพื่อแสดงกล่องข้อความให้ผู้ใช้เห็น

        self.resource_manager = pyvisa.ResourceManager("@py")   # "@py" หมายถึงให้ ใช้ pyvisa-py เป็น Backend (ระบบเบื้องหลัง)
        resources = self.resource_manager.list_resources()  # คำสั่ง list_resources() ให้ PyVISA แสดงรายการเครื่องมือทั้งหมดที่ค้นพบ
                                                            # หากยังไม่ได้ต่อเครื่อง อาจได้ ()
                                                            # หากต่อ RIGOL แล้ว อาจได้ประมาณนี้ ("USB0::6833::1200::DSxxxx::0::INSTR",)

        for resource in resources:  # ใช้วนตรวจสอบ Resource (ทรัพยากรหรืออุปกรณ์) ทีละรายการ
            if resource.startswith("USB"):  # คำสั่ง startswith("USB") ตรวจสอบว่าข้อความเริ่มต้นด้วย USB หรือไม่
                self.scope = self.resource_manager.open_resource(resource)  # open_resource() ใช้เปิดการเชื่อมต่อกับอุปกรณ์ที่เลือก เมื่อเชื่อมต่อสำเร็จ Object ของอุปกรณ์จะถูกเก็บไว้ใน self.scope
                                                                            # จากนี้ Method อื่น เช่น run_scope() และ stop_scope() จึงสามารถใช้ self.scope ส่งคำสั่งได้
                self.scope.timeout = 2000   # หากส่งคำสั่งแล้วเครื่องไม่ตอบภายใน 2 วินาที ให้ถือว่าเกิดข้อผิดพลาด
                return resource # เมื่อเชื่อมต่อสำเร็จ ฟังก์ชันจะส่งชื่อ Resource กลับไปยัง GUI
                                # ตัวอย่างชื่อที่ส่งกลับคือ
                                # USB0::6833::1200::DSxxxx::0::INSTR
                                # GUI จะนำข้อความนี้ไปแสดงในกล่อง

        # Close ResourceManager if no USB instrument is found.
        self.resource_manager.close()
        self.resource_manager = None

        raise RuntimeError("No USB instrument found.")

    def disconnect(self):
        """Disconnect from the oscilloscope."""
        # 1. ปิดการเชื่อมต่อกับเครื่อง RIGOL
        if self.scope is not None:
            self.scope.close()
            self.scope = None

        # 2. ปิด Resource Manager (ตัวจัดการอุปกรณ์)
        if self.resource_manager is not None:
            self.resource_manager.close()
            self.resource_manager = None
        
        
    def run_scope(self):
        """Start waveform acquisition."""
        if self.scope is None:
            raise RuntimeError("The oscilloscope is not connected.")

        self.scope.write(":RUN")    # :RUN เป็น SCPI command (คำสั่งมาตรฐานสำหรับควบคุมเครื่องมือวัด) ที่ตัวเครื่องรองรับอยู่แล้ว

    def stop_scope(self):
        """Stop waveform acquisition."""
        if self.scope is None:
            raise RuntimeError("The oscilloscope is not connected.")

        self.scope.write(":STOP")   # :STOP บอกให้ออสซิลโลสโคปหยุดรับข้อมูลรูปคลื่นใหม่
        # write() ใช้ส่งคำสั่งไปยังเครื่อง โดยไม่ได้รอข้อความตอบกลับ
        
        # กดปุ่ม Stop
        # → RigolGUI.stop_scope()
        # → ScopeController.stop_scope()
        # → self.scope.write(":STOP")

    def is_connected(self):
        """Return True when the oscilloscope is connected."""
        return self.scope is not None


class RigolGUI: #สร้างหน้าต่างและรับการกดปุ่ม
    """Graphical user interface for controlling a RIGOL oscilloscope."""

    def __init__(self, root):
        self.root = root
        self.controller = ScopeController()

        self.root.title("RIGOL Oscilloscope Controller")
        self.root.geometry("700x450")
        self.root.minsize(650, 420)

        self.create_widgets()
        self.update_connection_status(False) # ยังไม่ได้เชื่อมต่อกับออสซิลโลสโคป

        self.root.protocol("WM_DELETE_WINDOW", self.close_application)

    def create_widgets(self):
        """Create and arrange GUI widgets."""

        main_frame = ttk.Frame(self.root, padding=20)
        main_frame.pack(fill="both", expand=True)

        title_label = ttk.Label(
            main_frame,
            text="RIGOL Oscilloscope Controller",
            font=("Arial", 18, "bold")
        )
        title_label.pack(pady=(5, 20))

        status_frame = ttk.LabelFrame(
            main_frame,
            text="Connection Status",
            padding=15
        )
        status_frame.pack(fill="x", pady=(0, 15))

        self.status_label = ttk.Label(
            status_frame,
            text="DISCONNECTED",    # ตอนสร้าง status_label ครั้งแรก ให้แสดงข้อความว่า DISCONNECTED
            font=("Arial", 12, "bold")
        )
        self.status_label.pack()

        connection_frame = ttk.LabelFrame(
            main_frame,
            text="Connection Controls",
            padding=15
        )
        connection_frame.pack(fill="x", pady=(0, 15))

        self.connect_button = ttk.Button(   # ปุ่มนี้เรียกฟังก์ชัน connect_scope() ใน RigolGUI (คลาสหน้าต่างโปรแกรม) ก่อน จากนั้น connect_scope() จึงเรียก connect() ใน ScopeController อีกที
            connection_frame,
            text="Connect",
            command=self.connect_scope
        )
        self.connect_button.pack(side="left", expand=True, padx=5)  # นำปุ่ม Connect ไปแสดงในกรอบ วางเรียงจากทางซ้าย ให้ปุ่มได้รับส่วนแบ่งของพื้นที่ว่าง และเว้นระยะซ้าย–ขวา 5 พิกเซล

        self.disconnect_button = ttk.Button(
            connection_frame,
            text="Disconnect",
            command=self.disconnect_scope
        )
        self.disconnect_button.pack(side="left", expand=True, padx=5)

        control_frame = ttk.LabelFrame(
            main_frame,
            text="Oscilloscope Controls",
            padding=15
        )
        control_frame.pack(fill="x")

        self.run_button = ttk.Button(
            control_frame,
            text="Run",
            command=self.run_scope
        )
        self.run_button.pack(side="left", expand=True, padx=5)
        
#    ผู้ใช้กดปุ่ม Run
#        ↓
# RigolGUI.run_scope()
#        ↓
# ScopeController.run_scope()
#        ↓
# ตรวจสอบว่าเชื่อมต่อเครื่องแล้วหรือยัง
#        ↓
# ส่งคำสั่ง :RUN ผ่าน PyVISA และ USB
#        ↓
# ออสซิลโลสโคปเริ่มรับและอัปเดตรูปคลื่น
#        ↓
# GUI แสดงข้อความว่าส่งคำสั่งสำเร็จ

        self.stop_button = ttk.Button(
            control_frame,
            text="Stop",
            command=self.stop_scope
        )
        self.stop_button.pack(side="left", expand=True, padx=5)

    def update_connection_status(self, connected):
        """Update the connection status and button states."""

        if connected:
            self.status_label.config(text="CONNECTED")
            self.connect_button.config(state="disabled")    # connected แล้ว disable ปุ่ม connect เพราะทำ connect อยู่ ห้ามกดซ้ำ
            self.disconnect_button.config(state="normal")
            self.run_button.config(state="normal")
            self.stop_button.config(state="normal")
            # state="normal" หมายถึง เปิดให้กดปุ่มได้ตามปกติ
        else:
            self.status_label.config(text="DISCONNECTED")
            self.connect_button.config(state="normal")
            self.disconnect_button.config(state="disabled")
            self.run_button.config(state="disabled")
            self.stop_button.config(state="disabled")
            # state="disabled" หมายถึง ปุ่มถูกปิดใช้งานและกดไม่ได้

    def connect_scope(self):
        """Handle the Connect button."""

        # ให้ลองทำคำสั่งภายในก่อน หากเกิดข้อผิดพลาด ให้ไปทำส่วน except
        try:
            resource = self.controller.connect()
            self.update_connection_status(True)

            messagebox.showinfo(
                "Connection Successful",
                f"Connected to:\n{resource}"
            )

        except Exception as error:
            self.update_connection_status(False)

            messagebox.showerror(
                "Connection Error",
                str(error)
            )
# การใช้ try-except ช่วยให้โปรแกรมไม่ปิดตัวเองเมื่อเกิดปัญหา แต่แสดงกล่องข้อความแจ้งผู้ใช้แทน

    def disconnect_scope(self):
        """Handle the Disconnect button."""

        try:
            self.controller.disconnect()
            self.update_connection_status(False)

            messagebox.showinfo(
                "Disconnected",
                "The oscilloscope has been disconnected."
            )

        except Exception as error:
            messagebox.showerror(
                "Disconnection Error",
                str(error)
            )

    def run_scope(self):
        """Handle the Run button."""

        try:
            self.controller.run_scope()

            messagebox.showinfo(
                "Command Sent",
                "The :RUN command was sent successfully."
            )

        except Exception as error:
            messagebox.showerror(
                "Run Error",
                str(error)
            )

    def stop_scope(self):
        """Handle the Stop button."""

        try:
            self.controller.stop_scope()

            messagebox.showinfo(
                "Command Sent",
                "The :STOP command was sent successfully."
            )

        except Exception as error:
            messagebox.showerror(
                "Stop Error",
                str(error)
            )

    def close_application(self):
        """Disconnect safely before closing the application."""

        self.controller.disconnect()
        self.root.destroy()


def main():
    root = tk.Tk()
    RigolGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()