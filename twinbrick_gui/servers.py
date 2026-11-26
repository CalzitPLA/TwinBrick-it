"""The servers dialog: the hosts of ~/.ssh/config (their aliases only) and the servers set up here - pick a host, give
the path where TwinBrick it lives on it (and its Python), check it, copy this TwinBrick it there. Kept in
~/.config/twinbrick-it/servers.json (twinbrick.remote), never in the repository."""
from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

from twinbrick import remote


class ServersDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Servers (ssh)")
        self.resize(720, 420)
        lay = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel("Hosts come from your ssh config - only their aliases are read; ssh resolves the "
                                "rest and needs a key or an agent. A server builds the whole site (it downloads the "
                                "data itself) in a temporary folder that is removed afterwards. 'Copy TwinBrick it "
                                "there' puts this version and its brick engine into the path.")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Host", "Path", "Python", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._picked)
        lay.addWidget(self.table, 1)
        form = QtWidgets.QFormLayout()
        self.host = QtWidgets.QLineEdit()
        self.path = QtWidgets.QLineEdit(remote.DEFAULT_PATH)
        self.python = QtWidgets.QLineEdit("python3")
        form.addRow("Host (ssh alias)", self.host)
        form.addRow("Path on the server", self.path)
        form.addRow("Python on the server", self.python)
        lay.addLayout(form)
        row = QtWidgets.QHBoxLayout()
        for text, fn in (("Save", self.save), ("Check", self.check), ("Copy TwinBrick it there", self.sync),
                         ("Remove", self.remove)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.status = {}
        self.refresh()

    def refresh(self):
        servers = remote.load_servers()
        hosts = remote.known_hosts()
        self.table.setRowCount(len(hosts))
        for r, h in enumerate(hosts):
            s = servers.get(h)
            for c, text in enumerate((h, s.path if s else "", s.python if s else "",
                                      self.status.get(h, "set up" if s else ""))):
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(text))

    def _picked(self):
        rows = {i.row() for i in self.table.selectedItems()}
        if not rows:
            return
        r = rows.pop()
        self.host.setText(self.table.item(r, 0).text())
        s = remote.load_servers().get(self.host.text())
        self.path.setText(s.path if s else remote.DEFAULT_PATH)
        self.python.setText(s.python if s else "python3")

    def _run(self, what, fn):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            return fn()
        except Exception as ex:  # noqa: BLE001 - shown to the user
            QtWidgets.QMessageBox.warning(self, "Servers", f"{what}: {ex}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def save(self):
        if self._run("save", lambda: remote.add_server(self.host.text(), self.path.text(), self.python.text())):
            self.refresh()

    def check(self):
        self.save()
        info = self._run("check", lambda: remote.check(self.host.text()))
        if info:
            self.status[self.host.text()] = info.get("message", "")
            self.refresh()

    def sync(self):
        self.save()
        if self._run("copy", lambda: remote.sync_code(self.host.text())):
            self.status[self.host.text()] = "copied - check it"
            self.refresh()

    def remove(self):
        remote.remove_server(self.host.text())
        self.status.pop(self.host.text(), None)
        self.refresh()
