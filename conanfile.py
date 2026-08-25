from conan import ConanFile
from conan.errors import ConanException, ConanInvalidConfiguration
from conan.tools.cmake import CMake, CMakeDeps, CMakeToolchain
from conan.tools.files import copy, save
import os
import contextlib


class ReLCSConan(ConanFile):
    name = "reLCS"
    version = "master"
    license = "???"  # FIXME: https://github.com/GTAmodding/re3/issues/794
    settings = "os", "arch", "compiler", "build_type"
    generators = "CMakeDeps", "CMakeToolchain"
    options = {
        "audio": ["openal", "miles"],
        "with_libsndfile": [True, False],
        "with_opus": [True, False],
    }
    default_options = {
        "audio": "openal",
        "with_libsndfile": False,
        "with_opus": False,
        # "libsndfile:with_external_libs": False,
        # "mpg123:flexible_resampling": False,
        # "mpg123:network": False,
        # "mpg123:icy": False,
        # "mpg123:id3v2": False,
        # "mpg123:ieeefloat": False,
        # "mpg123:layer1": False,
        # "mpg123:layer2": False,
        # "mpg123:layer3": False,
        # "mpg123:moreinfo": False,
        # "sdl2:vulkan": False,
        # "sdl2:opengl": True,
        # "sdl2:sdl2main": True,
    }
    no_copy_source = True

    def layout(self):
        # Pin generator output to a predictable "generators" subfolder of
        # whatever --output-folder the caller passes to `conan install`,
        # rather than relying on cmake_layout()'s build-type-dependent
        # folder naming.
        self.folders.generators = "generators"

    @property
    def _os_is_playstation2(self):
        try:
            return self.settings.os == "Playstation2"
        except ConanException:
            return False

    def configure(self):
        if self.options.audio != "openal":
            self.options.with_libsndfile = False

    def requirements(self):
        self.requires("librw/{}".format(self.version))
        self.requires("mpg123/1.26.4")
        if self.options.audio == "openal":
            self.requires("openal/1.21.0")
        elif self.options.audio == "miles":
            self.requires("miles-sdk/{}".format(self.version))
        if self.options.with_libsndfile:
            self.requires("libsndfile/1.0.30")
        if self.options.with_opus:
            self.requires("opusfile/0.12")

    def export_sources(self):
        for d in ("cmake", "src"):
            copy(self, "*", src=os.path.join(self.recipe_folder, d),
                 dst=os.path.join(self.export_sources_folder, d))
        copy(self, "CMakeLists.txt", src=self.recipe_folder, dst=self.export_sources_folder)

    def validate(self):
        # NOTE: accessing a dependency's options in validate() requires the
        # dependency graph to already be resolved for this node, which it
        # is by the time validate() runs post-requirements(). If this turns
        # out to raise (e.g. `librw` not found in self.dependencies yet in
        # some Conan versions), move this check into generate() instead.
        librw = self.dependencies["librw"]
        if librw.options.platform == "gl3" and librw.options.gl3_gfxlib != "glfw":
            raise ConanInvalidConfiguration("Only `glfw` is supported as gl3_gfxlib.")
        #if not self.options.with_opus:
        #    if not self.dependencies["libsndfile"].options.with_external_libs:
        #        raise ConanInvalidConfiguration("reLCS with opus support requires a libsndfile built with external libs (=ogg/flac/opus/vorbis)")

    @property
    def _reLCS_audio(self):
        return {
            "miles": "MSS",
            "openal": "OAL",
        }[str(self.options.audio)]

    @contextlib.contextmanager
    def _environment_append(self, env):
        # Replacement for the removed conans.tools.environment_append
        # context manager: temporarily set env vars, restore previous
        # values (or unset) on exit.
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            yield
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def build(self):
        if self.source_folder == self.build_folder:
            raise Exception("cannot build with source_folder == build_folder")

        librw = self.dependencies["librw"]

        # These Find*.cmake shims existed to paper over Conan 1's
        # `cmake_find_package` generator not matching upstream module/target
        # names. CMakeDeps (Conan 2) generates proper Config-mode files with
        # correct upstream target names (e.g. OpenAL::OpenAL, glfw), so these
        # are very likely no longer necessary. Left in place â adapted to
        # the new save() API â since removing them is unverified without
        # your actual CMakeLists.txt in front of me; try dropping this block
        # first and only keep it if find_package(OpenAL)/find_package(glfw3)
        # breaks in your CMakeLists.txt.
        save(self, os.path.join(self.build_folder, "FindOpenAL.cmake"),
             "\n".join([
                 "set(OPENAL_FOUND ON)",
                 "set(OPENAL_INCLUDE_DIR ${OpenAL_INCLUDE_DIRS})",
                 "set(OPENAL_LIBRARY ${OpenAL_LIBRARIES})",
                 "set(OPENAL_DEFINITIONS ${OpenAL_DEFINITIONS})",
                 "",
             ]), append=True)
        if librw.options.platform == "gl3" and librw.options.gl3_gfxlib == "glfw":
            save(self, os.path.join(self.build_folder, "Findglfw3.cmake"),
                 "\n".join([
                     "if(NOT TARGET glfw)",
                     '  message(STATUS "Creating glfw TARGET")',
                     "  add_library(glfw INTERFACE IMPORTED)",
                     "  set_target_properties(glfw PROPERTIES",
                     "     INTERFACE_LINK_LIBRARIES CONAN_PKG::glfw)",
                     "endif()",
                     "",
                 ]), append=True)

        cmake = CMake(self)
        cmake.definitions["RELCS_AUDIO"] = self._reLCS_audio
        cmake.definitions["RELCS_WITH_OPUS"] = self.options.with_opus
        cmake.definitions["RELCS_INSTALL"] = True
        cmake.definitions["RELCS_VENDORED_LIBRW"] = False

        env = {}
        if self._os_is_playstation2:
            # TODO: these two lines depend on how the ps2dev-cmaketoolchain /
            # ps2dev-ps2sdk recipes expose their values under Conan 2.
            # `deps_user_info` and `deps_cpp_info` no longer exist. The
            # modern equivalent is almost always exposing a value via
            # `self.conf_info` in *those* recipes' package_info(), read here
            # as e.g. self.dependencies["ps2dev-cmaketoolchain"].conf_info.get(
            #     "user.ps2dev-cmaketoolchain:cmake_toolchain_file"
            # ). Left unresolved since it depends on migrating that recipe
            # too, and PS2 isn't in your active CI matrix.
            cmake.definitions["CMAKE_TOOLCHAIN_FILE"] = None  # FIXME: see above
            env["PS2SDK"] = self.dependencies["ps2dev-ps2sdk"].package_folder

        # Conan 2's CMake helper wires up the generated toolchain file
        # automatically (via CMakeToolchain in `generators`), so unlike the
        # Conan 1 version we can point configure() straight at the real
        # source tree instead of building a synthetic wrapper CMakeLists.txt.
        with self._environment_append(env):
            cmake.configure(build_script_folder=self.source_folder)
        cmake.build()

    def package(self):
        cmake = CMake(self)
        cmake.install()
