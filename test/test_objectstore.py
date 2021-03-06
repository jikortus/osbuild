import os
import shutil
import tempfile
import unittest

from pathlib import Path
from osbuild import objectstore


class TestObjectStore(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.store = os.getenv("OSBUILD_TEST_STORE")
        if not cls.store:
            cls.store = tempfile.mkdtemp(prefix="osbuild-test-", dir="/var/tmp")

    @classmethod
    def tearDownClass(cls):
        if not os.getenv("OSBUILD_TEST_STORE"):
            shutil.rmtree(cls.store)

    def test_basic(self):
        # always use a temporary store so item counting works
        with tempfile.TemporaryDirectory(dir="/var/tmp") as tmp:
            object_store = objectstore.ObjectStore(tmp)
            # No objects or references should be in the store
            assert len(os.listdir(object_store.refs)) == 0
            assert len(os.listdir(object_store.objects)) == 0

            with object_store.new() as tree:
                with tree.write() as path:
                    p = Path(f"{path}/A")
                    p.touch()
                object_store.commit(tree, "a")

            assert object_store.contains("a")
            assert os.path.exists(f"{object_store.refs}/a")
            assert os.path.exists(f"{object_store.refs}/a/A")
            assert len(os.listdir(object_store.refs)) == 1
            assert len(os.listdir(object_store.objects)) == 1
            assert len(os.listdir(f"{object_store.refs}/a/")) == 1

            with object_store.new() as tree:
                with tree.write() as path:
                    p = Path(f"{path}/A")
                    p.touch()
                    p = Path(f"{path}/B")
                    p.touch()
                object_store.commit(tree, "b")

            assert object_store.contains("b")
            assert os.path.exists(f"{object_store.refs}/b")
            assert os.path.exists(f"{object_store.refs}/b/B")

            assert len(os.listdir(object_store.refs)) == 2
            assert len(os.listdir(object_store.objects)) == 2
            assert len(os.listdir(f"{object_store.refs}/b/")) == 2

            self.assertEqual(object_store.resolve_ref(None), None)
            self.assertEqual(object_store.resolve_ref("a"),
                             f"{object_store.refs}/a")

    def test_cleanup(self):
        # always use a temporary store so item counting works
        with tempfile.TemporaryDirectory(dir="/var/tmp") as tmp:
            with objectstore.ObjectStore(tmp) as object_store:
                tree = object_store.new()
                self.assertEqual(len(os.listdir(object_store.tmp)), 1)
                with tree.write() as path:
                    p = Path(f"{path}/A")
                    p.touch()
            # there should be no temporary Objects dirs anymore
            self.assertEqual(len(os.listdir(object_store.tmp)), 0)

    def test_duplicate(self):
        with tempfile.TemporaryDirectory(dir="/var/tmp") as tmp:
            object_store = objectstore.ObjectStore(tmp)
            with object_store.new() as tree:
                with tree.write() as path:
                    p = Path(f"{path}/A")
                    p.touch()
                object_store.commit(tree, "a")

            with object_store.new() as tree:
                with tree.write() as path:
                    shutil.copy2(f"{object_store.refs}/a/A",
                                 f"{path}/A")
                object_store.commit(tree, "b")

            assert os.path.exists(f"{object_store.refs}/a")
            assert os.path.exists(f"{object_store.refs}/a/A")
            assert os.path.exists(f"{object_store.refs}/b/A")

            assert len(os.listdir(object_store.refs)) == 2
            assert len(os.listdir(object_store.objects)) == 1
            assert len(os.listdir(f"{object_store.refs}/a/")) == 1
            assert len(os.listdir(f"{object_store.refs}/b/")) == 1

    def test_object_base(self):
        # operate with a clean object store
        with tempfile.TemporaryDirectory(dir="/var/tmp") as tmp:
            object_store = objectstore.ObjectStore(tmp)
            with object_store.new() as tree:
                with tree.write() as path:
                    p = Path(f"{path}/A")
                    p.touch()
                object_store.commit(tree, "a")

            with object_store.new() as tree:
                tree.base = "a"
                object_store.commit(tree, "b")

            with object_store.new() as tree:
                tree.base = "b"
                with tree.write() as path:
                    p = Path(f"{path}/C")
                    p.touch()
                object_store.commit(tree, "c")

            assert os.path.exists(f"{object_store.refs}/a/A")
            assert os.path.exists(f"{object_store.refs}/b/A")
            assert os.path.exists(f"{object_store.refs}/c/A")
            assert os.path.exists(f"{object_store.refs}/c/C")

            assert len(os.listdir(object_store.refs)) == 3
            assert len(os.listdir(object_store.objects)) == 2

    def test_object_copy_on_write(self):
        # operate with a clean object store
        with tempfile.TemporaryDirectory(dir="/var/tmp") as tmp:
            # sample data to be used for read, write checks
            data = "23"

            object_store = objectstore.ObjectStore(tmp)
            assert len(os.listdir(object_store.refs)) == 0

            with object_store.new() as tree:
                path = tree.write()
                with tree.write() as path, \
                     open(f"{path}/data", "w") as f:
                    f.write(data)
                    st = os.fstat(f.fileno())
                    data_inode = st.st_ino
                # commit the object as "x"
                x_hash = object_store.commit(tree, "x")
                # after the commit, "x" is now the base
                # of "tree"
                self.assertEqual(tree.base, "x")
                # check that "data" is still the very
                # same file after committing
                with tree.read() as path:
                    with open(f"{path}/data", "r") as f:
                        st = os.fstat(f.fileno())
                        self.assertEqual(st.st_ino, data_inode)
                        data_read = f.read()
                        self.assertEqual(data, data_read)

            # the object referenced by "x" should act as
            # the base of a new object. As long as the
            # new one is not modified, it should have
            # the very same content
            with object_store.new(base_id="x") as tree:
                self.assertEqual(tree.base, "x")
                self.assertEqual(tree.treesum, x_hash)
                with tree.read() as path:
                    with open(f"{path}/data", "r") as f:
                        # copy-on-write: since we have not written
                        # to the tree yet, "data" should be the
                        # very same file as that one of object "x"
                        st = os.fstat(f.fileno())
                        self.assertEqual(st.st_ino, data_inode)
                        data_read = f.read()
                        self.assertEqual(data, data_read)
                with tree.write() as path:
                    # "data" must of course still be present
                    assert os.path.exists(f"{path}/data")
                    # but since it is a copy, have a different inode
                    st = os.stat(f"{path}/data")
                    self.assertNotEqual(st.st_ino, data_inode)
                    p = Path(f"{path}/other_data")
                    p.touch()
                # now that we have written, the treesum
                # should have changed
                self.assertNotEqual(tree.treesum, x_hash)

    def test_object_mode(self):
        object_store = objectstore.ObjectStore(self.store)
        with object_store.new() as tree:
            # check that trying to write to a tree that is
            # currently being read from fails
            with tree.read() as _:
                with self.assertRaises(ValueError):
                    with tree.write() as _:
                        pass

                # check multiple readers are ok
                with tree.read() as _:
                    # calculating the treesum also is reading,
                    # so this is 3 nested readers
                    _ = tree.treesum

                # writing should still fail
                with self.assertRaises(ValueError):
                    with tree.write() as _:
                        pass

            # Now that all readers are gone, writing should
            # work
            with tree.write() as _:
                pass

            # and back to reading, one last time
            with tree.read() as _:
                with self.assertRaises(ValueError):
                    with tree.write() as _:
                        pass

            # Only one single writer
            with tree.write() as _:
                # no other readers
                with self.assertRaises(ValueError):
                    with tree.read() as _:
                        pass

                # or other writers
                with self.assertRaises(ValueError):
                    with tree.write() as _:
                        pass

            # one more time
            with tree.write() as _:
                pass

        # tree has exited the context, it should NOT be
        # writable anymore
        with self.assertRaises(ValueError):
            with tree.write() as _:
                pass

    def test_snapshot(self):
        object_store = objectstore.ObjectStore(self.store)
        with object_store.new() as tree:
            with tree.write() as path:
                p = Path(f"{path}/A")
                p.touch()
            assert not object_store.contains("a")
            object_store.commit(tree, "a")
            assert object_store.contains("a")
            with tree.write() as path:
                p = Path(f"{path}/B")
                p.touch()
            object_store.commit(tree, "b")

        # check the references exist
        assert os.path.exists(f"{object_store.refs}/a")
        assert os.path.exists(f"{object_store.refs}/b")

        # check the contents of the trees
        assert os.path.exists(f"{object_store.refs}/a/A")
        assert not os.path.exists(f"{object_store.refs}/a/B")

        assert os.path.exists(f"{object_store.refs}/b/A")
        assert os.path.exists(f"{object_store.refs}/b/B")

    def test_host_tree(self):
        object_store = objectstore.ObjectStore(self.store)
        host = objectstore.HostTree(object_store)

        # check we cannot call `write`
        with self.assertRaises(ValueError):
            with host.write() as _:
                pass

        # check we actually cannot write to the path
        with host.read() as path:
            p = Path(f"{path}/osbuild-test-file")
            with self.assertRaises(OSError):
                p.touch()


if __name__ == "__main__":
    unittest.main()
